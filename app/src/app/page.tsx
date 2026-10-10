import { WorkspaceShell } from "@/components/workspace-shell";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { getAuth } from "@/server/auth";

export default async function Home() {
  const session = await getAuth().api.getSession({ headers: await headers() });
  if (!session) redirect("/login");
  return <WorkspaceShell accountSubject={session.user.id} />;
}
