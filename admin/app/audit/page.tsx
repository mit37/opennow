import { cookies } from 'next/headers';
import { createServerComponentClient } from '@supabase/auth-helpers-nextjs';

type AuditRow = {
  id: number;
  ts: string;
  admin_id: string | null;
  table_name: string;
  row_id: string;
  diff: Record<string, unknown>;
};

export default async function AuditPage() {
  const supabase = createServerComponentClient({ cookies });

  const { data, error } = (await supabase
    .from('audit_log')
    .select('id, ts, admin_id, table_name, row_id, diff')
    .order('ts', { ascending: false })
    .limit(100)) as unknown as {
    data: AuditRow[] | null;
    error: { message: string } | null;
  };

  if (error) {
    return <p className="text-sm text-red-600">Failed to load audit log: {error.message}</p>;
  }

  const rows = data ?? [];

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Audit Log</h1>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-left">
            <th className="py-2 pr-4">Time</th>
            <th className="py-2 pr-4">Admin</th>
            <th className="py-2 pr-4">Table</th>
            <th className="py-2 pr-4">Row</th>
            <th className="py-2 pr-4">Diff</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-gray-100 align-top">
              <td className="whitespace-nowrap py-2 pr-4">{new Date(row.ts).toLocaleString()}</td>
              <td className="py-2 pr-4">{row.admin_id ?? 'System'}</td>
              <td className="py-2 pr-4">{row.table_name}</td>
              <td className="py-2 pr-4">{row.row_id}</td>
              <td className="py-2 pr-4">
                <pre className="max-w-md whitespace-pre-wrap break-words text-xs">
                  {JSON.stringify(row.diff, null, 2)}
                </pre>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={5} className="py-4 text-gray-500">
                No audit entries yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
