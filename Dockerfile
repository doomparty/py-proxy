# 使用官方 Python 轻量级镜像
FROM python:3.9-slim

# 设置环境变量，确保 Python 输出直接打印到控制台（方便看日志）
ENV PYTHONUNBUFFERED=1

# 设置工作目录
WORKDIR /app

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制主程序代码
COPY ..

# 暴露端口 3000
EXPOSE 3000

# 启动命令
CMD ["python", "main.py"]
