import { proxyPlatform } from "@/lib/platform/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Context = { params: Promise<{ path: string[] }> };
async function handle(request: Request, context: Context) {
  return proxyPlatform(request, (await context.params).path);
}

export { handle as DELETE, handle as GET, handle as POST, handle as PUT };
