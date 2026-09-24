import { cookies } from 'next/headers';
import { createServerComponentClient } from '@supabase/auth-helpers-nextjs';
import ClosuresManager, { type ServiceOption } from './ClosuresManager';

export default async function ClosuresPage() {
  const supabase = createServerComponentClient({ cookies });

  const { data, error } = (await supabase
    .from('service')
    .select('id, name')
    .order('name')) as unknown as {
    data: ServiceOption[] | null;
    error: { message: string } | null;
  };

  if (error) {
    return <p className="text-sm text-red-600">Failed to load services: {error.message}</p>;
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Closures</h1>
      <ClosuresManager services={data ?? []} />
    </div>
  );
}
