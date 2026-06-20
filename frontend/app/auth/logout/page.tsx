"use client";

import { useEffect } from "react";
import { logout } from "../../lib/auth";
import { useRouter } from "next/navigation";

export default function LogoutPage() {
  const router = useRouter();

  useEffect(() => {
    logout().finally(() => router.replace("/"));
  }, [router]);

  return (
    <div className="min-h-screen bg-zinc-50 p-6">
      <main className="max-w-3xl mx-auto bg-white rounded-3xl p-6 shadow-sm">
        <h1 className="text-3xl font-bold mb-4">Cerrando sesión...</h1>
      </main>
    </div>
  );
}
