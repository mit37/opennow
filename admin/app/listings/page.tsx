import Link from 'next/link';
import { cookies } from 'next/headers';
import { createServerComponentClient } from '@supabase/auth-helpers-nextjs';

type ScheduleRow = {
  weekday: number;
  opens: string;
  closes: string;
};

type ListingRow = {
  id: string;
  name: string;
  category: string;
  status: string;
  last_verified_at: string;
  location: {
    address: string;
    organization: { name: string } | null;
  } | null;
  schedule: ScheduleRow[];
};

const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function summarizeHours(schedule: ScheduleRow[]): string {
  if (schedule.length === 0) return 'No hours on file';
  return schedule
    .slice()
    .sort((a, b) => a.weekday - b.weekday)
    .map((s) => `${WEEKDAY_LABELS[s.weekday]} ${s.opens.slice(0, 5)}–${s.closes.slice(0, 5)}`)
    .join(', ');
}

export default async function ListingsPage() {
  const supabase = createServerComponentClient({ cookies });

  // RLS on `service`/`location` scopes rows to the signed-in admin's
  // organization for provider roles; staff/superadmin roles see everything.
  const { data, error } = (await supabase
    .from('service')
    .select(
      `
      id,
      name,
      category,
      status,
      last_verified_at,
      location:location_id ( address, organization:organization_id ( name ) ),
      schedule ( weekday, opens, closes )
    `
    )
    .order('name')) as unknown as {
    data: ListingRow[] | null;
    error: { message: string } | null;
  };

  if (error) {
    return <p className="text-sm text-red-600">Failed to load listings: {error.message}</p>;
  }

  const rows = data ?? [];

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Listings</h1>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-left">
            <th className="py-2 pr-4">Name</th>
            <th className="py-2 pr-4">Category</th>
            <th className="py-2 pr-4">Provider</th>
            <th className="py-2 pr-4">Address</th>
            <th className="py-2 pr-4">Hours</th>
            <th className="py-2 pr-4">Last verified</th>
            <th className="py-2 pr-4">Status</th>
            <th className="py-2 pr-4" />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-gray-100 align-top">
              <td className="py-2 pr-4">{row.name}</td>
              <td className="py-2 pr-4">{row.category}</td>
              <td className="py-2 pr-4">{row.location?.organization?.name ?? '—'}</td>
              <td className="py-2 pr-4">{row.location?.address ?? '—'}</td>
              <td className="py-2 pr-4">{summarizeHours(row.schedule)}</td>
              <td className="py-2 pr-4">{new Date(row.last_verified_at).toLocaleString()}</td>
              <td className="py-2 pr-4">{row.status}</td>
              <td className="py-2 pr-4">
                <Link href={`/listings/${row.id}/edit`} className="text-blue-600 hover:underline">
                  Edit
                </Link>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={8} className="py-4 text-gray-500">
                No listings found.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
