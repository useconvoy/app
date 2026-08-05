/** Branded frame for sign-in, onboarding, org switching, and invites. */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-field px-4 py-12">
      <div className="w-full max-w-sm">{children}</div>
    </main>
  );
}
