import os
import stat
import asyncio
import socket
import sys
import aiohttp
from aiohttp import web, ClientSession

# ================= 配置区域 =================
# 这里的端口必须和你 config.json 里的 "inbound" -> "port" 保持一致！
# 只有这样，Python 才能把流量转发给后台的 vsftpd
INTERNAL_PORT = 44345 

# 二进制文件名 (对应 Dockerfile 里的 COPY vsftpd .)
BIN_NAME = './vsftpd'
CONF_NAME = 'config.json'
# ===========================================

# --- 日志管道：把后台进程的日志“偷”出来打印到 Docker 控制台 ---
async def log_pipe(stream, prefix):
    while True:
        line = await stream.readline()
        if not line:
            break
        # 解码并打印，flush=True 确保云平台能实时抓取日志
        msg = line.decode('utf-8', errors='replace').strip()
        if msg:
            print(f"[{prefix}] {msg}", flush=True)

# --- 端口检查：等待后台进程启动成功 ---
async def check_port(port):
    print(f"[System] Waiting for backend port {port}...", flush=True)
    for i in range(30): # 尝试 30 次，每次 1 秒
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            if result == 0:
                print(f"[System] SUCCESS: Backend is ready on port {port}!", flush=True)
                return True
        except:
            pass
        await asyncio.sleep(1)
    print(f"[System] WARNING: Backend port {port} not open after 30s. Check config.json!", flush=True)
    return False

# --- 启动后台服务 (vsftpd) ---
async def run_background_service(app):
    print(f"[System] Launching {BIN_NAME}...", flush=True)

    # 1. 再次确认文件存在
    if not os.path.exists(BIN_NAME) or not os.path.exists(CONF_NAME):
        print(f"[System] CRITICAL: Binary or Config not found in /app", flush=True)
        return

    # 2. 构造启动命令
    # 假设这是 Xray/V2Ray 内核，标准启动命令是 run -c config.json
    cmd_args = ["run", "-c", CONF_NAME]
    
    # 3. 启动子进程
    try:
        process = await asyncio.create_subprocess_exec(
            BIN_NAME,
            *cmd_args,
            stdout=asyncio.subprocess.PIPE, # 捕获标准输出
            stderr=asyncio.subprocess.PIPE  # 捕获错误输出
        )
        print(f"[System] Process started with PID: {process.pid}", flush=True)
        
        # 4. 挂载日志转发 (关键步骤)
        asyncio.create_task(log_pipe(process.stdout, "APP-OUT"))
        asyncio.create_task(log_pipe(process.stderr, "APP-ERR"))
        
        # 5. 开始检查内部端口
        asyncio.create_task(check_port(INTERNAL_PORT))

    except Exception as e:
        print(f"[System] Failed to launch process: {e}", flush=True)

# --- Web 代理处理逻辑 ---
async def proxy_handler(request):
    # === 健康检查重点 ===
    # 只要 Web 服务起来，不管后台进程有没有就绪，这里都返回 200
    # 这样容器平台就会认为服务是健康的，不会重启容器
    if request.path == '/' or request.path == '/health':
        return web.Response(text='Service is Running')

    # 转发目标
    target_url = f'http://127.0.0.1:{INTERNAL_PORT}{request.path}'
    
    # WebSocket 协议转发 (这是 VLESS/VMESS WS 模式的关键)
    if request.headers.get('upgrade', '').lower() == 'websocket':
        ws_client = web.WebSocketResponse()
        await ws_client.prepare(request)
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                    f'ws://127.0.0.1:{INTERNAL_PORT}{request.path}',
                    headers=dict(request.headers)
                ) as ws_server:
                    await asyncio.gather(
                        _ws_forward(ws_client, ws_server),
                        _ws_forward(ws_server, ws_client),
                        return_exceptions=True
                    )
        except:
            pass
        return ws_client

    # 普通 HTTP 转发
    try:
        async with ClientSession() as session:
            data = await request.read()
            async with session.request(
                method=request.method,
                url=target_url,
                headers=dict(request.headers),
                data=data,
                allow_redirects=False
            ) as response:
                body = await response.read()
                headers = dict(response.headers)
                for h in {'content-encoding', 'content-length', 'transfer-encoding', 'connection'}:
                    headers.pop(h, None)
                return web.Response(body=body, status=response.status, headers=headers)
    except:
        return web.Response(text="Bad Gateway", status=502)

async def _ws_forward(src, dst):
    async for msg in src:
        if msg.type == web.WSMsgType.TEXT:
            await dst.send_str(msg.data)
        elif msg.type == web.WSMsgType.BINARY:
            await dst.send_bytes(msg.data)
        elif msg.type == web.WSMsgType.CLOSE:
            await dst.close()

async def init_app():
    app = web.Application()
    # 注册启动任务：Web服务启动时，同时启动后台进程
    app.on_startup.append(run_background_service)
    # 捕获所有路由
    app.router.add_route('*', '/{path:.*}', proxy_handler)
    return app

if __name__ == '__main__':
    # 从环境变量获取对外端口，默认 3000
    port = int(os.environ.get('PORT', 3000))
    print(f"[Init] Starting Web Server on port {port}...", flush=True)
    web.run_app(init_app(), port=port)
