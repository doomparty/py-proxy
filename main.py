import os
import stat
import asyncio
import socket
from aiohttp import web
from aiohttp import ClientSession

# --- 辅助功能：像 tail -f 一样实时读取日志文件 ---
async def monitor_log_file(filepath):
    print(f"[LogMonitor] Waiting for {filepath} to be created...")
    # 等待文件创建（给进程一点时间来创建文件）
    retries = 0
    while not os.path.exists(filepath):
        await asyncio.sleep(0.5)
        retries += 1
        if retries > 10:
            print(f"[LogMonitor] Timeout waiting for {filepath}")
            return
    
    print(f"[LogMonitor] Start tailing {filepath}...")
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            while True:
                line = f.readline()
                if line:
                    print(f"[VSFTPD-LOG] {line.strip()}")
                else:
                    # 如果读到了末尾，等待新内容
                    await asyncio.sleep(0.5)
    except Exception as e:
        print(f"[LogMonitor] Error reading log: {e}")

# --- 辅助功能：检查端口是否开放 ---
async def check_port(port, retries=20, delay=1):
    print(f"[System] Checking if port {port} is listening...")
    for i in range(retries):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            if result == 0:
                print(f"[System] SUCCESS: Port {port} is OPEN and listening!")
                return True
        except Exception:
            pass
        await asyncio.sleep(delay)
    print(f"[System] WARNING: Port {port} is NOT responding after {retries} seconds. Check vsftpd.log for errors.")
    return False

# --- 核心逻辑：启动本地服务 ---
async def run_vsftpd_service(app):
    bin_name = 'vsftpd'
    conf_name = 'config.json'
    log_file = 'vsftpd.log'
    
    print(f"[System] Initializing local service...")

    # 1. 检查文件是否存在 (Docker COPY 应该已经放进去了)
    if not os.path.exists(bin_name):
        print(f"[System] CRITICAL ERROR: Binary '{bin_name}' not found in /app directory!")
        return
    if not os.path.exists(conf_name):
        print(f"[System] WARNING: Config '{conf_name}' not found. Verify if binary needs it.")

    # 2. 赋予执行权限 (chmod +x)
    try:
        st = os.stat(bin_name)
        os.chmod(bin_name, st.st_mode | stat.S_IEXEC)
        print("[System] chmod +x applied to binary.")
    except Exception as e:
        print(f"[System] Failed to chmod binary: {e}")

    # 3. 执行命令并重定向日志
    # 命令：./vsftpd run -c ./config.json > vsftpd.log 2>&1
    cmd = f"./{bin_name} run -c ./{conf_name} > {log_file} 2>&1"
    
    print(f"[System] Executing command: {cmd}")
    
    # 启动日志监控任务
    asyncio.create_task(monitor_log_file(log_file))

    try:
        # 异步启动子进程
        process = await asyncio.create_subprocess_shell(cmd)
        print(f"[System] Process started with PID: {process.pid}")
        
        # 4. 检查端口 44345 是否通了
        asyncio.create_task(check_port(44345))

    except Exception as e:
        print(f"[System] Failed to execute command: {e}")

# --- 代理处理逻辑 (保持不变) ---
async def proxy_handler(request):
    if request.path == '/':
        return web.Response(text='Hello World')

    target_url = f'http://127.0.0.1:44345{request.path}'
    
    # WebSocket 代理
    if request.headers.get('upgrade', '').lower() == 'websocket':
        ws_client = web.WebSocketResponse()
        await ws_client.prepare(request)
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                    f'ws://127.0.0.1:44345{request.path}',
                    headers=dict(request.headers)
                ) as ws_server:
                    await asyncio.gather(
                        _ws_forward(ws_client, ws_server),
                        _ws_forward(ws_server, ws_client),
                        return_exceptions=True
                    )
        except Exception:
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
    # 注册启动任务
    app.on_startup.append(run_vsftpd_service)
    app.router.add_route('*', '/{path:.*}', proxy_handler)
    return app

if __name__ == '__main__':
    web.run_app(init_app(), port=3000)
