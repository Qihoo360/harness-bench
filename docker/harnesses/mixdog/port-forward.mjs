// TCP forwarder: 127.0.0.1:LISTEN → HOST:DSTPORT (inside the harness container).
// Mixdog's transport policy allows plain HTTP only on loopback; the per-trial
// model proxy listens on the host, so the launcher fronts it with this
// loopback forwarder and points the provider at http://127.0.0.1:LISTEN.
import net from 'node:net';

const [listenPort, dstHost, dstPort] = [Number(process.argv[2]), process.argv[3], Number(process.argv[4])];
if (!listenPort || !dstHost || !dstPort) {
  console.error('usage: port-forward.mjs <listen-port> <dst-host> <dst-port>');
  process.exit(64);
}
const server = net.createServer({ allowHalfOpen: false }, client => {
  const upstream = net.connect({ host: dstHost, port: dstPort });
  client.on('error', () => upstream.destroy());
  upstream.on('error', () => client.destroy());
  client.pipe(upstream);
  upstream.pipe(client);
  const closeBoth = () => { client.destroy(); upstream.destroy(); };
  client.on('close', closeBoth);
  upstream.on('close', closeBoth);
});
server.listen(listenPort, '127.0.0.1', () => console.log('forwarder-ready'));
