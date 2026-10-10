import { LibraryManager } from "@/components/library-manager";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { getAuth } from "@/server/auth";

export default async function LibraryPage() {
  if (!await getAuth().api.getSession({ headers: await headers() })) redirect("/login?next=/library");
  return <LibraryManager />;
}
