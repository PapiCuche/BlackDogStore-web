FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --legacy-peer-deps || true
COPY . .
EXPOSE 3000
CMD ["sh", "-c", "npm run dev -- --host 0.0.0.0"]
