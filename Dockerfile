# 两阶段构建：besdk 是 git+https 依赖，pip install 时要有 git；git 与编译
# 工具链只留在 build 阶段，不进最终镜像。
FROM python:3.12-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml ./
COPY backend ./backend
COPY gen ./gen
RUN pip install --no-cache-dir --prefix=/install .

# 基础镜像必须带 /bin/sh 与 wget：平台的健康检查是 CMD-SHELL 调 wget。
FROM python:3.12-slim

# WeasyPrint 的系统库与中文字体。只装 pango / cairo / gdk-pixbuf 不装 CJK 字体时，
# 中文 PDF 退化成 DejaVu，pdftotext 提取出来是乱码且重复（不是方框）；
# fonts-noto-cjk + fontconfig + fc-cache -f 缺一不可。外壳 be/py-render 的镜像要装同一份。
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 libgdk-pixbuf-2.0-0 libcairo2 \
    fontconfig fonts-noto-cjk \
    wget ca-certificates tzdata \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# build 与运行是同一个基础镜像，--prefix=/install 装出的 site-packages 路径与这里的
# 解释器一致，搬到 /usr/local 就在默认 sys.path 上。
COPY --from=build /install /usr/local

WORKDIR /app
# migration.command（python -m infra_print.migrate apply）在 WORKDIR 下读 migrations/；
# run_standalone 从工作目录的 component.yaml 读要监听的端口，漏拷它容器启动即退出。
COPY migrations ./migrations
COPY component.yaml ./

EXPOSE 8400 9400

ENTRYPOINT ["python", "-m", "infra_print.main"]
