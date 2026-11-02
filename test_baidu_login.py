# test_baidu_login.py
"""测试百度登录并获取BDUSS

WARNING: 百度有严格的反爬机制，此脚本可能无法完全模拟登录。
主要挑战:
1. 百度使用浏览器指纹识别
2. 经常需要 CAPTCHA 验证码
3. 新设备可能需要短信/邮件验证
4. 登录过程有多次重定向

如果自动登录失败，请使用浏览器手动获取 Cookie。
"""
import requests
import re
import json
import logging
from urllib.parse import urlencode

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 百度登录相关URL
PASSPORT_URL = "https://passport.baidu.com"
LOGIN_API_URL = f"{PASSPORT_URL}/v2/api/?login"
GET_API_URL = f"{PASSPORT_URL}/v2/api/?getapi"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.baidu.com/",
}


def get_token(session: requests.Session) -> dict:
    """获取登录token"""
    params = {
        "getapi": "",
        "tpl": "netdisk",
        "apiver": "v3",
        "tt": str(int(requests.utils.time.time() * 1000)),
        "class": "login",
        "logintype": "dialogLogin",
        "tplreg": "netdisk",
        "regType": "1",
        "callback": "bd__cbs__dpD7JG",
    }
    url = f"{GET_API_URL}&{urlencode(params)}"

    resp = session.get(url, headers=HEADERS, timeout=30)
    logger.info(f"Token请求状态: {resp.status_code}")

    # 从响应中提取token
    match = re.search(r'"token"\s*:\s*"([^"]+)"', resp.text)
    if match:
        return {"token": match.group(1), "cookies": dict(resp.cookies)}

    # 尝试从HTML中提取
    match = re.search(r"bd__cbs__dpD7JG\((.*?)\)", resp.text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except:
            pass

    logger.warning(f"未找到token, 响应: {resp.text[:500]}")
    return {}


def login_with_password(session: requests.Session, username: str, password: str, token: str = None) -> dict:
    """使用用户名密码登录"""
    if not token:
        token_data = get_token(session)
        token = token_data.get("token", "")

    if not token:
        logger.error("无法获取登录token")
        return {"success": False, "error": "无法获取登录token"}

    # 登录POST数据
    login_data = {
        "username": username,
        "password": password,
        "token": token,
        "logintype": "dialogLogin",
        "tpl": "netdisk",
        "apiver": "v3",
        "callback": "parent.bd__cbs__x",
        # 百度可能需要的其他字段
        "isphone": "false",
        "logintype": "dialogLogin",
        "tt": str(int(requests.utils.time.time() * 1000)),
    }

    logger.info(f"尝试登录用户: {username}")

    resp = session.post(
        LOGIN_API_URL,
        data=login_data,
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=True,
        timeout=30
    )

    logger.info(f"登录响应状态: {resp.status_code}")
    logger.info(f"登录响应URL: {resp.url}")

    # 检查响应
    cookies = session.cookies.get_dict()
    logger.info(f"登录后Cookies: {list(cookies.keys())}")

    # 查找BDUSS
    if "BDUSS" in cookies:
        logger.info("成功获取BDUSS!")
        return {
            "success": True,
            "bduss": cookies["BDUSS"],
            "ptoken": cookies.get("ptoken", ""),
            "cookies": cookies,
        }

    # 检查响应内容中是否有错误信息
    errno_match = re.search(r'"errno"\s*:\s*(\d+)', resp.text)
    if errno_match:
        errno = errno_match.group(1)
        logger.error(f"登录失败 errno={errno}")

    # 尝试解析JSON响应
    try:
        # 去掉callback包装
        json_text = re.sub(r"^[^(]*\(", "", resp.text)
        json_text = json_text.rstrip(");")
        data = json.loads(json_text)
        if data.get("errno"):
            logger.error(f"登录错误: {data}")
    except:
        pass

    logger.warning(f"登录响应: {resp.text[:1000]}")
    return {"success": False, "error": "登录未返回BDUSS", "response_text": resp.text[:500]}


def try_baijiahao_login(username: str, password: str) -> dict:
    """尝试登录百家号"""
    session = requests.Session()

    logger.info("=== 步骤1: 获取登录Token ===")
    token_info = get_token(session)
    if not token_info.get("token"):
        logger.error("获取token失败，尝试直接登录...")

    logger.info("=== 步骤2: 执行登录 ===")
    result = login_with_password(
        session,
        username,
        password,
        token_info.get("token", "")
    )

    if result["success"]:
        logger.info("=" * 50)
        logger.info("🎉 登录成功!")
        logger.info(f"BDUSS: {result['bduss'][:50]}...")
        logger.info(f"ptoken: {result.get('ptoken', '')[:50] if result.get('ptoken') else 'N/A'}...")
        logger.info("=" * 50)
    else:
        logger.error("=" * 50)
        logger.error(f"❌ 登录失败: {result.get('error', '未知错误')}")
        logger.error("=" * 50)

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("用法: python test_baidu_login.py <用户名> <密码>")
        print("示例: python test_baidu_login.py test@163.com mypassword")
        sys.exit(1)

    username = sys.argv[1]
    password = sys.argv[2]

    result = try_baijiahao_login(username, password)

    if result["success"]:
        print("\n✅ 登录成功!")
        print(f"BDUSS: {result['bduss']}")
        print(f"ptoken: {result.get('ptoken', '')}")
    else:
        print(f"\n❌ 登录失败: {result.get('error', '未知错误')}")
        print("\n提示: 百度反爬严格，自动登录可能失败。")
        print("建议手动从浏览器获取BDUSS和ptoken:")
        print("1. 登录 baijiahao.baidu.com")
        print("2. F12 -> Network -> 刷新 -> 查看任意请求的Cookie")