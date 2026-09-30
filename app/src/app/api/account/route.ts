import { AuthenticationRequiredError, requireAuthenticatedUser } from "@/server/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const headers = { "Cache-Control": "private, no-store" };
  try {
    return Response.json({ user: await requireAuthenticatedUser(request) }, { headers });
  } catch (error) {
    if (error instanceof AuthenticationRequiredError) {
      return Response.json({ error: { code: "authentication_required", message: "请先登录。" } },
        { status: 401, headers });
    }
    return Response.json({ error: { code: "account_unavailable", message: "暂时无法读取账号。" } },
      { status: 503, headers });
  }
}
