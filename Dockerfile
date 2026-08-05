# 知识图谱网站静态托管镜像
# 构建： docker build -t goty-knowledge-graph .
# 运行： docker run -d -p 8080:80 --name goty-graph goty-knowledge-graph
# 访问： http://localhost:8080
FROM nginx:1.27-alpine

# 移除默认配置，使用自定义配置（gzip + 长缓存静态资源）
RUN rm /etc/nginx/conf.d/default.conf
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

# 站点文件（src/build_site.py 生成的 site/ 目录）
COPY site/ /usr/share/nginx/html/

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
