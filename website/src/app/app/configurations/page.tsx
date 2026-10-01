import { Suspense } from "react";
import { ConfigurationsIndexPage } from "@/components/configurations/pages/ConfigurationsIndexPage";

export default function ConfigurationsPage() {
  return <Suspense fallback={null}><ConfigurationsIndexPage /></Suspense>;
}
