import { Suspense } from "react";
import { NewConfigurationPage } from "@/components/configurations/pages/NewConfigurationPage";

export default function NewConfigurationRoute() {
  return <Suspense fallback={null}><NewConfigurationPage /></Suspense>;
}
