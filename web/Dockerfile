# Dev-oriented image: runs the Vite dev server. A prod image would build + serve
# static assets via nginx; kept simple for Phase 0.
FROM node:20-alpine

WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev"]
