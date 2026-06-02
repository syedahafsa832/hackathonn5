FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install --force
COPY . .
ARG REACT_APP_API_BASE_URL=http://localhost:8001
ENV REACT_APP_API_BASE_URL=$REACT_APP_API_BASE_URL
RUN npm run build
FROM nginx:alpine
COPY --from=0 /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]