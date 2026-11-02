"""admin 账号密码改为由 ADMIN_DEFAULT_PASSWORD 环境变量决定

背景
----
初始 admin 账号的密码哈希原先在迁移 ``0004`` 里**硬编码**为 ``"admin123"``，
于是：

- 改 ``.env`` 的 ``ADMIN_DEFAULT_PASSWORD`` 对已有 DB 毫无作用
- 该密码随仓库公开，任何人拿到仓库就能以 admin/admin123 登录管理后台
- ``app/api/v1/auth.py`` 里"仍在使用默认密码"的告警用 ``==`` 比较 bcrypt 哈希，
  而 bcrypt 每次加盐结果都不同 → 永远为 False，告警从未触发过

本迁移做两件事：

1. 如果 admin 账号的密码**仍是旧的出厂默认值**（用 checkpw 验证明文
   ``"admin123"``，而不是比较哈希字符串），把它重写成
   ``ADMIN_DEFAULT_PASSWORD`` 的哈希。
   已经自行改过密码的账号**不会**被覆盖。
2. 顺带修掉登录告警的哈希比较 bug（见 ``app/api/v1/auth.py``）。

只有当 ``ADMIN_DEFAULT_PASSWORD`` 仍是出厂默认 ``"admin123"`` 时才不动 ——
那种情况下没什么可改的，保留原样即可。
"""
import sqlalchemy as sa
from alembic import op

revision = "admin_password_from_env"
down_revision = "remove_dead_system_settings"

# 旧的出厂默认密码（明文），仅用于 checkpw 判断"用户是否还没改过密码"
_LEGACY_DEFAULT_PLAINTEXT = "admin123"


def upgrade():
    from app.core.config import settings
    from app.core.security import hash_password, verify_password

    new_password = settings.admin_default_password
    if not new_password or new_password == _LEGACY_DEFAULT_PLAINTEXT:
        # 没有自定义密码，或仍是出厂默认 → 无需处理
        return

    conn = op.get_bind()
    row = conn.execute(
        sa.text("SELECT id, password_hash FROM admin_users WHERE username = 'admin'")
    ).fetchone()
    if row is None:
        # admin 账号不存在（例如手动删过）→ 什么都不做
        return

    user_id, current_hash = row[0], row[1]
    try:
        still_legacy = verify_password(_LEGACY_DEFAULT_PLAINTEXT, current_hash)
    except Exception:  # noqa: BLE001
        still_legacy = False

    if not still_legacy:
        # 管理员已经自行改过密码 → 不要覆盖
        return

    conn.execute(
        sa.text("UPDATE admin_users SET password_hash = :h WHERE id = :i"),
        {"h": hash_password(new_password), "i": user_id},
    )
    print(
        "[admin_password_from_env] admin 账号密码已更新为 ADMIN_DEFAULT_PASSWORD 的哈希"
    )


def downgrade():
    # 不回退密码: 迁移前后的密码都是部署者自己配置的值，
    # 回退成 "admin123" 反而会把弱密码写回去。
    pass
