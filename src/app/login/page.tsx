import { Suspense } from "react";
import { AuthForm } from "@/components/AuthForm";

export default function LoginPage() {
  return (
    <main className="auth-page">
      <Suspense>
        <AuthForm mode="login" />
      </Suspense>
    </main>
  );
}
