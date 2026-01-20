# 使用官方轻量 Python 镜像
FROM python:3.9-slim

# 设置环境变量，让 Python 日志立即输出
ENV PYTHONUNBUFFERED=1

# 设置工作目录
WORKDIR /app

# 1. 复制依赖并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. 复制核心文件
# 务必确保这三个文件在 Dockerfile 同级目录下
COPY vsftpd .
COPY config.json .
COPY main.py .

# 暴露端口
EXPOSE 3000

# 启动
CMD ["python", "main.py"]
