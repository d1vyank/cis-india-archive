// Serves /files/* (attachments) from the R2 bucket bound as FILES (Pages project ->
// Settings -> Bindings -> R2 bucket, variable name FILES). Object keys are "files/<path>",
// as uploaded by tools/upload_files_r2.sh.
//
// Why a Function and not a public bucket: on the Workers Free plan, Functions stop at
// 100,000 requests a day (error 1027) instead of billing. That caps R2 reads at about
// 3.1 million a month, under R2's 10 million free Class B operations, so traffic can
// never push R2 past its free tier. Keep the bucket's r2.dev URL and custom domains off.
export async function onRequest({ request, env }) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405, headers: { allow: "GET, HEAD" } });
  }
  let key;
  try {
    key = decodeURIComponent(new URL(request.url).pathname).replace(/^\/+/, "");
  } catch {
    return new Response("Bad request", { status: 400 });
  }
  if (!key.startsWith("files/") || key.includes("..")) {
    return new Response("Not found", { status: 404 });
  }
  const obj = request.method === "HEAD"
    ? await env.FILES.head(key)
    : await env.FILES.get(key, { range: request.headers, onlyIf: request.headers });
  if (obj === null) {
    return new Response("Not found", { status: 404 });
  }
  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("etag", obj.httpEtag);
  headers.set("accept-ranges", "bytes");
  headers.set("cache-control", "public, max-age=31536000, immutable");  // files never change
  if (request.method === "HEAD") {
    headers.set("content-length", String(obj.size));
    return new Response(null, { headers });
  }
  if (!("body" in obj)) {   // If-None-Match / If-Modified-Since matched
    return new Response(null, { status: 304, headers });
  }
  if (obj.range && request.headers.has("range")) {
    const start = obj.range.offset ?? (obj.size - obj.range.suffix);
    const length = obj.range.length ?? (obj.size - start);
    headers.set("content-range", `bytes ${start}-${start + length - 1}/${obj.size}`);
    headers.set("content-length", String(length));
    return new Response(obj.body, { status: 206, headers });
  }
  headers.set("content-length", String(obj.size));
  return new Response(obj.body, { headers });
}
