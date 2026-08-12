import { redirect } from "next/navigation";

/** The Systems page became Connectors; old links and bookmarks follow. */
export default function SystemsRedirect() {
  redirect("/app/connectors");
}
