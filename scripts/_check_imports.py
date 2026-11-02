"""一次性脚本: 验证 5 个改动的函数/方法/正则/配置都到位.

不真正 import 业务模块 (避免触发 slowapi / redis 等重依赖),
改用 ast 静态解析 + 源码字符串检查.
"""
import ast
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _extract_module(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    funcs = set()
    classes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            funcs.add(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.add(node.name)
    return {"funcs": funcs, "classes": classes}


print("=" * 60)
print("[1/5] webhook.py 静态校验")
m = _extract_module(os.path.join(ROOT, "app/api/v1/webhook.py"))
required_funcs = {
    "_handle_subscribe", "_handle_unsubscribe",
    "_handle_click_event", "_verify_signature", "_build_text_reply",
}
missing = required_funcs - m["funcs"]
assert not missing, f"webhook.py 缺函数: {missing}"
src = open(os.path.join(ROOT, "app/api/v1/webhook.py"), encoding="utf-8").read()
assert "CHOOSE_TOPIC_(\\d+)" in src, "webhook.py 缺 EventKey 正则"
assert "UserSubscription" in src, "webhook.py 未引用 UserSubscription"
assert "UserChoice" in src, "webhook.py 未引用 UserChoice"
print("      函数齐全:", sorted(required_funcs))
print("      EventKey 正则 + UserSubscription/UserChoice 都已引用 ✓")

print("=" * 60)
print("[2/5] kafka_producer.py 静态校验")
m = _extract_module(os.path.join(ROOT, "app/services/kafka_producer.py"))
required = {
    "send_crawl_topics", "send_generate_request", "send_article_generated",
    "send_publish_request", "send_system_event",
}
missing = required - m["funcs"]
assert not missing, f"kafka_producer.py 缺方法: {missing}"
print("      5 个 send_* 方法齐全 ✓")

print("=" * 60)
print("[3/5] article_tasks.py 静态校验")
src = open(os.path.join(ROOT, "app/tasks/article_tasks.py"), encoding="utf-8").read()
assert "send_generate_request" in src, "process_user_choices 没改用 Kafka"
assert "producer.flush" in src, "Kafka 消息应 flush 再 commit"
# 用 AST 提取函数体源码 (不包括 docstring), 严格区分"提到"和"实际调用"
with open(os.path.join(ROOT, "app/tasks/article_tasks.py"), encoding="utf-8") as f:
    tree = ast.parse(f.read())
proc_node = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "process_user_choices":
        proc_node = node
        break
assert proc_node is not None, "找不到 process_user_choices 函数定义"
# 用 ast.unparse 生成 body, 跳过 docstring (body[0] 通常是 Expr[Constant str])
body_nodes = proc_node.body
if (body_nodes and isinstance(body_nodes[0], ast.Expr)
        and isinstance(body_nodes[0].value, ast.Constant)
        and isinstance(body_nodes[0].value.value, str)):
    body_nodes = body_nodes[1:]
body_src = ast.unparse(ast.Module(body=body_nodes, type_ignores=[]))
assert "generate_article.delay" not in body_src, \
    "process_user_choices 函数体仍在调 generate_article.delay, 没切到 Kafka"
assert "send_generate_request" in body_src, "函数体没调 send_generate_request"
print("      process_user_choices 已切到 Kafka 生产 ✓")

print("=" * 60)
print("[4/5] publish_consumer.py 静态校验")
m = _extract_module(os.path.join(ROOT, "consumers/publish_consumer.py"))
required_classes = {"PublishConsumer"}
required_funcs = {"start", "_commit", "process_message",
                  "_send_publish_success_notification"}
assert required_classes <= m["classes"], f"publish_consumer.py 缺类: {required_classes - m['classes']}"
assert required_funcs <= m["funcs"], f"publish_consumer.py 缺方法: {required_funcs - m['funcs']}"
src = open(os.path.join(ROOT, "consumers/publish_consumer.py"), encoding="utf-8").read()
assert "wechat:lock:publish_article" in src, "publish_consumer 没引用 Redis 锁"
assert "acquire_lock_sync" in src, "publish_consumer 没调 acquire_lock_sync"
assert "release_lock_sync" in src, "publish_consumer 没调 release_lock_sync"
assert "kafka_topic_publish" in src, "publish_consumer 没订阅 kafka_topic_publish"
print("      PublishConsumer 类齐全, Redis 锁 + Kafka 订阅都有 ✓")

print("=" * 60)
print("[5/5] docker-compose.yml 静态校验")
src = open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8").read()
assert "consumer-publish:" in src, "docker-compose.yml 缺 consumer-publish 服务"
assert "publish_consumer" in src, "consumer-publish command 没指向 publish_consumer"
assert "ENABLE_AUTO_PUBLISH:-true" in src, "ENABLE_AUTO_PUBLISH 默认值还是 false"
assert "container_name: wechat-consumer-publish" in src, "consumer-publish 容器名未设置"
print("      consumer-publish 服务已注入, ENABLE_AUTO_PUBLISH 默认 true ✓")

print("=" * 60)
print("全部通过 ✅ 改动到位, 可以进入部署阶段")