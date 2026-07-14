// Tiny sample "generated app": a static HTTP server the Playwright check drives.
// Node core only (no deps) — proves Node runs inside the sandbox.
const http = require('http');

const PORT = Number(process.env.SMOKE_PORT || 3123);
const HOST = '127.0.0.1';

const server = http.createServer((_req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end('<!doctype html><html><body><h1 id="status">ok</h1></body></html>');
});

server.listen(PORT, HOST, () => {
  console.log(`server-listening http://${HOST}:${PORT}`);
});
