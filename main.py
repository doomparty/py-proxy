import os
import stat
import asyncio
import socket
from aiohttp import web
from aiohttp import ClientSession

# --- 功能：实时监控日志文件 (Tail -f) ---
async def monitor_log_file(filepath):
    print(f"[LogMonitor] Waiting for {filepath} generation...")
    
    # 等待日志文件被创建
    retries = 0
    while not os.path.exists(filepath):
        await asyncio.sleep(0.5)
        retries += 1
        if retries > 20: # 等待10秒还没文件说明启动失败了
            print(f"[LogMonitor] Error: Log file {filepath} was not created.")
            return

    print(f"[LogMonitor] Tailing {filepath}...")
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            while True:
                line = f.readline()
                if line:
                    # 打印到 Docker 控制台 (带前缀方便区分)
                    print(f"[VSFTPD] {line.strip()}")
                else:
                    # 读到末尾，暂停一下等待新日志写入
                    await asyncio.sleep(0.5)
    except Exception as e:
        print(f"[LogMonitor] Error reading log: {e}")

# --- 功能：检查端口连通性 ---
async def check_port(port):
    print(f"[System] Waiting for port {port}...")
    for i in range(30): # 尝试 30 次 (30秒)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            if result == 0:
                print(f"[System] SUCCESS: Port {port} is OPEN! Service is ready.")
                return True
        except:
            pass
        await asyncio.sleep(1)
    print(f"[System] WARNING: Port {port} did not open after 30 seconds.")
    return False

# --- 核心任务：启动 vsftpd ---
async def run_vsftpd_service(app):
    bin_name = 'vsftpd'
    conf_name = 'config.json'
    log_file = 'vsftpd.log'
    
    print(f"[System] Initializing service...")

    # 1. 确保文件存在
    if not os.path.exists(bin_name):
        print(f"[System] ERROR: {bin_name} not found. Did you COPY it in Dockerfile?")
        return

    # 2. 赋予可执行权限 (chmod +x)
    try:
        st = os.stat(bin_name)
        os.chmod(bin_name, st.st_mode | stat.S_IEXEC)
        print(f"[System] Granted executable permissions to ./{bin_name}")
    except Exception as e:
        print(f"[System] Failed to chmod: {e}")

    # 3. 构造启动命令
    # 格式：./vsftpd run -c ./config.json > vsftpd.log 2>&1
    cmd = f"./{bin_name} run -c ./{conf_name} > {log_file} 2>&1"
    
    print(f"[System] Executing: {cmd}")
    
    # 4. 启动日志监控 (异步)
    asyncio.create_task(monitor_log_file(log_file))

    try:
        # 5. 执行命令 (异步非阻塞)
        process = await asyncio.create_subprocess_shell(cmd)
        print(f"[System] Process launched with PID: {process.pid}")
        
        # 6. 开始检查端口 (假设 config.json 里配的是 44520)
        asyncio.create_task(check_port(44520))

    except Exception as e:
        print(f"[System] Failed to launch process: {e}")

# --- 代理服务逻辑 (保持原样) ---
async def proxy_handler(request):
    if request.path == '/':
        return web.Response(text='Hello World')

    target_url = f'http://127.0.0.1:44520{request.path}'
    
    # WebSocket 代理
    if request.headers.get('upgrade', '').lower() == 'websocket':
        ws_client = web.WebSocketResponse()
        await ws_client.prepare(request)
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                    f'ws://127.0.0.1:44520{request.path}',
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

    # HTTP 代理
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
    except Exception as e:
        return web.Response(text=f"Proxy Error: {e}", status=502)

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
    app.on_startup.append(run_vsftpd_service)
    app.router.add_route('*', '/{path:.*}', proxy_handler)
    return app

if __name__ == '__main__':
    web.run_app(init_app(), port=3000)
