import type { Metadata } from 'next';
import Link from 'next/link';
import './globals.css';

export const metadata: Metadata = {
  title: 'OpenNow Admin',
  description: 'Staff admin for OpenNow service listings, hours and closures',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-50 text-gray-900">
        <header className="border-b border-gray-200 bg-white">
          <nav className="mx-auto flex max-w-5xl items-center gap-6 px-4 py-3">
            <span className="font-semibold">OpenNow Admin</span>
            <Link href="/listings" className="text-sm text-gray-700 hover:underline">
              Listings
            </Link>
            <Link href="/closures" className="text-sm text-gray-700 hover:underline">
              Closures
            </Link>
            <Link href="/audit" className="text-sm text-gray-700 hover:underline">
              Audit Log
            </Link>
          </nav>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
