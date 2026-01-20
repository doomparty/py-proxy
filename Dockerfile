FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# 安装常用工具 (curl, ps, netstat) 用于排查
RUN apt-get update && apt-get install -y procps curl net-tools && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- 关键修改：直接复制本地的二进制和配置文件 ---
COPY vsftpd .
COPY config.json .
COPY main.py .

# 暴露端口
EXPOSE 3000

# 启动
CMD ["python", "main.py"]
