"""remove dead system_settings rows

背景
----
管理后台「系统配置」页面渲染的是 ``system_settings`` 表的每一行，但这些行
对应的运行时配置**从来不会被读取**：

- ``deepseek_api_key`` / ``deepseek_model`` / ``deepseek_temperature`` /
  ``deepseek_max_tokens``：LLM 走 ``llm_providers`` 表，
  ``settings.deepseek_*`` 在运行时代码里没有任何读取点。
- ``enable_crawler`` / ``enable_deepseek``：两个死开关，没有读取点。

留着它们只会让管理员以为「在这里改能生效」，实际改了没有任何效果。
本次迁移把这 6 行删掉，让管理后台只显示真正生效的配置项。

``app.core.runtime_overrides`` 负责把剩下的行覆盖到运行时 ``settings`` 上，
其中 ``wechat_app_id`` / ``wechat_app_secret`` 留空时不覆盖（沿用 ``.env``）。
"""
from alembic import op

revision = "remove_dead_system_settings"
down_revision = "merge_platform_ip_whitelist"

DEAD_KEYS = [
    "deepseek_api_key",
    "deepseek_model",
    "deepseek_temperature",
    "deepseek_max_tokens",
    "enable_crawler",
    "enable_deepseek",
]

# 供 downgrade 还原（与 2026_07_24_0000 种子数据一致）
SEED_ROWS = [
    ("deepseek_api_key", "", "sk-0****abcd", "deepseek", "DeepSeek API Key", True, "sk-..."),
    ("deepseek_model", "deepseek-chat", "", "deepseek", "DeepSeek 模型", False, "deepseek-chat"),
    ("deepseek_temperature", "0.7", "", "deepseek", "生成温度", False, "0.0-1.0"),
    ("deepseek_max_tokens", "3000", "", "deepseek", "最大 Token 数", False, "整数"),
    ("enable_crawler", "true", "", "system", "启用爬虫", False, "true/false"),
    ("enable_deepseek", "true", "", "system", "启用 AI 生成", False, "true/false"),
]


def upgrade():
    op.execute(
        "DELETE FROM system_settings WHERE key IN ({})".format(
            ", ".join("'%s'" % k for k in DEAD_KEYS)
        )
    )


def downgrade():
    for key, enc, masked, cat, desc, is_sec, placeholder in SEED_ROWS:
        op.execute(
            """
            INSERT INTO system_settings
                (key, value_encrypted, value_masked, category, description,
                 is_secret, placeholder, updated_at)
            VALUES ('{key}', '{enc}', '{masked}', '{cat}', '{desc}',
                    {is_sec}, '{placeholder}', NOW())
            ON CONFLICT (key) DO NOTHING
            """.format(
                key=key,
                enc=enc,
                masked=masked,
                cat=cat,
                desc=desc,
                is_sec="true" if is_sec else "false",
                placeholder=placeholder,
            )
        )
