import os
import stat
import asyncio
import socket
import sys
from aiohttp import web
from aiohttp import ClientSession

# --- 核心修改：直接将子进程日志打印到标准输出 (STDOUT) ---
async def log_pipe(stream, prefix):
    """
    读取子进程的输出流，并直接 print 到控制台，
    这样容器平台的日志系统才能抓取到。
    """
    while True:
        line = await stream.readline()
        if not line:
            break
        # 解码并去除末尾换行符
        msg = line.decode('utf-8', errors='replace').strip()
        if msg:
            # flush=True 是关键，确保日志立即显示，不要缓存
            print(f"[{prefix}] {msg}", flush=True)

# --- 端口检查 ---
async def check_port(port):
    print(f"[System] Waiting for port {port} to open...", flush=True)
    for i in range(30):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            if result == 0:
                print(f"[System] SUCCESS: Port {port} is OPEN! Service is ready.", flush=True)
                return True
        except:
            pass
        await asyncio.sleep(1)
    print(f"[System] WARNING: Port {port} did not open after 30s.", flush=True)
    return False

# --- 启动服务 ---
async def run_vsftpd_service(app):
    bin_name = 'vsftpd'
    conf_name = 'config.json'
    
    print(f"[System] Initializing service on Container Platform...", flush=True)

    if not os.path.exists(bin_name):
        print(f"[System] CRITICAL: {bin_name} not found!", flush=True)
        return

    # 1. 赋予权限
    try:
        st = os.stat(bin_name)
        os.chmod(bin_name, st.st_mode | stat.S_IEXEC)
        print(f"[System] Permission granted to ./{bin_name}", flush=True)
    except Exception as e:
        print(f"[System] chmod failed: {e}", flush=True)

    # 2. 启动进程 (使用 PIPE 而不是重定向到文件)
    # 假设你的 config.json 里配置的是 44345 端口
    cmd_args = ["run", "-c", f"./{conf_name}"]
    
    print(f"[System] Executing: ./{bin_name} {' '.join(cmd_args)}", flush=True)

    try:
        # 使用 exec 配合 PIPE，不要用 shell=True，这样更稳定
        process = await asyncio.create_subprocess_exec(
            f"./{bin_name}",
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        print(f"[System] Process started with PID: {process.pid}", flush=True)
        
        # 3. 启动日志转发任务 (把 xray 的日志打到控制台)
        asyncio.create_task(log_pipe(process.stdout, "VSFTPD-OUT"))
        asyncio.create_task(log_pipe(process.stderr, "VSFTPD-ERR"))
        
        # 4. 检查端口
        asyncio.create_task(check_port(44345))

    except Exception as e:
        print(f"[System] Failed to launch process: {e}", flush=True)

# --- 代理逻辑 ---
async def proxy_handler(request):
    if request.path == '/':
        return web.Response(text='App is Running')

    target_url = f'http://127.0.0.1:44345{request.path}'
    
    # WebSocket
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
        except:
            pass
        return ws_client

    # HTTP
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
        print(f"[ProxyError] {e}", flush=True)
        return web.Response(text=f"Proxy Error", status=502)

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
    # 适配平台动态端口，如果没有环境变量则默认 3000
    port = int(os.environ.get('PORT', 3000))
    print(f"[Init] Starting web server on port {port}...", flush=True)
    web.run_app(init_app(), port=port)
