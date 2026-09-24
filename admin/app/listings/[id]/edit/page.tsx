import { cookies } from 'next/headers';
import { notFound } from 'next/navigation';
import { createServerComponentClient } from '@supabase/auth-helpers-nextjs';
import EditListingForm, { type CategoryOption, type StatusOption } from './EditListingForm';

type ScheduleRow = {
  weekday: number;
  opens: string;
  closes: string;
};

type ServiceRow = {
  id: string;
  name: string;
  category: string;
  status: string;
  eligibility_note: string | null;
  schedule: ScheduleRow[];
};

const CATEGORIES: CategoryOption[] = ['food', 'pantry', 'shower', 'dropin', 'wifi', 'clothes'];
const STATUSES: StatusOption[] = ['active', 'hidden', 'retired'];

type EditListingPageProps = {
  params: { id: string };
};

export default async function EditListingPage({ params }: EditListingPageProps) {
  const supabase = createServerComponentClient({ cookies });

  const { data, error } = (await supabase
    .from('service')
    .select(
      `
      id,
      name,
      category,
      status,
      eligibility_note,
      schedule ( weekday, opens, closes )
    `
    )
    .eq('id', params.id)
    .maybeSingle()) as unknown as {
    data: ServiceRow | null;
    error: { message: string } | null;
  };

  if (error) {
    return <p className="text-sm text-red-600">Failed to load listing: {error.message}</p>;
  }

  if (!data) {
    notFound();
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Edit listing</h1>
      <EditListingForm service={data} categories={CATEGORIES} statuses={STATUSES} />
    </div>
  );
}
