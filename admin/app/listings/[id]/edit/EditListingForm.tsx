'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { supabase } from '@/lib/supabaseClient';

export type CategoryOption = 'food' | 'pantry' | 'shower' | 'dropin' | 'wifi' | 'clothes';
export type StatusOption = 'active' | 'hidden' | 'retired';

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

type TimeWindow = {
  key: string;
  opens: string;
  closes: string;
};

type WeekSchedule = TimeWindow[][];

type EditListingFormProps = {
  service: ServiceRow;
  categories: CategoryOption[];
  statuses: StatusOption[];
};

const WEEKDAY_LABELS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

let windowKeySeq = 0;
function nextWindowKey(): string {
  windowKeySeq += 1;
  return `w${windowKeySeq}`;
}

function toTimeInputValue(value: string): string {
  return value.length >= 5 ? value.slice(0, 5) : value;
}

function buildInitialWeek(schedule: ScheduleRow[]): WeekSchedule {
  const week: WeekSchedule = [[], [], [], [], [], [], []];
  for (const row of schedule) {
    if (row.weekday < 0 || row.weekday > 6) continue;
    week[row.weekday].push({
      key: nextWindowKey(),
      opens: toTimeInputValue(row.opens),
      closes: toTimeInputValue(row.closes),
    });
  }
  for (const day of week) {
    day.sort((a, b) => a.opens.localeCompare(b.opens));
  }
  return week;
}

type SaveMessage = { kind: 'success' | 'error'; text: string };

export default function EditListingForm({ service, categories, statuses }: EditListingFormProps) {
  const router = useRouter();
  const [name, setName] = useState(service.name);
  const [category, setCategory] = useState(service.category);
  const [status, setStatus] = useState(service.status);
  const [eligibilityNote, setEligibilityNote] = useState(service.eligibility_note ?? '');
  const [week, setWeek] = useState<WeekSchedule>(() => buildInitialWeek(service.schedule));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<SaveMessage | null>(null);

  function addWindow(day: number) {
    setWeek((prev) => {
      const next = prev.map((windows) => windows.slice());
      next[day].push({ key: nextWindowKey(), opens: '', closes: '' });
      return next;
    });
  }

  function removeWindow(day: number, key: string) {
    setWeek((prev) => {
      const next = prev.map((windows) => windows.slice());
      next[day] = next[day].filter((w) => w.key !== key);
      return next;
    });
  }

  function updateWindow(day: number, key: string, field: 'opens' | 'closes', value: string) {
    setWeek((prev) => {
      const next = prev.map((windows) => windows.slice());
      next[day] = next[day].map((w) => (w.key === key ? { ...w, [field]: value } : w));
      return next;
    });
  }

  function validate(): string | null {
    if (name.trim() === '') return 'Name is required.';
    for (let day = 0; day < 7; day += 1) {
      for (const w of week[day]) {
        if (!w.opens || !w.closes) {
          return `${WEEKDAY_LABELS[day]}: every window needs both an opens and a closes time.`;
        }
        if (w.opens >= w.closes) {
          return `${WEEKDAY_LABELS[day]}: opens time must be before closes time.`;
        }
      }
    }
    return null;
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);

    const validationError = validate();
    if (validationError) {
      setMessage({ kind: 'error', text: validationError });
      return;
    }

    setSaving(true);

    const { error: updateError } = await supabase
      .from('service')
      .update({
        name: name.trim(),
        category,
        status,
        eligibility_note: eligibilityNote.trim() === '' ? null : eligibilityNote.trim(),
      })
      .eq('id', service.id);

    if (updateError) {
      setSaving(false);
      setMessage({ kind: 'error', text: `Failed to save listing details: ${updateError.message}` });
      return;
    }

    const { error: deleteError } = await supabase.from('schedule').delete().eq('service_id', service.id);

    if (deleteError) {
      setSaving(false);
      setMessage({
        kind: 'error',
        text: `Listing details saved, but failed to clear old hours: ${deleteError.message}`,
      });
      return;
    }

    const rows = week.flatMap((windows, weekday) =>
      windows.map((w) => ({
        service_id: service.id,
        weekday,
        opens: w.opens,
        closes: w.closes,
      }))
    );

    if (rows.length > 0) {
      const { error: insertError } = await supabase.from('schedule').insert(rows);
      if (insertError) {
        setSaving(false);
        setMessage({
          kind: 'error',
          text: `Listing details saved, but failed to save hours: ${insertError.message}`,
        });
        return;
      }
    }

    setSaving(false);
    setMessage({ kind: 'success', text: 'Saved. Changes are live now.' });
    router.refresh();
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="flex flex-col text-sm">
          Name
          <input
            type="text"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="mt-1 rounded border border-gray-300 px-3 py-2"
          />
        </label>
        <label className="flex flex-col text-sm">
          Category
          <select
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            className="mt-1 rounded border border-gray-300 px-3 py-2"
          >
            {categories.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm">
          Status
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            className="mt-1 rounded border border-gray-300 px-3 py-2"
          >
            {statuses.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm sm:col-span-2">
          Eligibility note
          <input
            type="text"
            value={eligibilityNote}
            onChange={(event) => setEligibilityNote(event.target.value)}
            placeholder="e.g. families only, ID not required"
            className="mt-1 rounded border border-gray-300 px-3 py-2"
          />
        </label>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">Weekly hours</h2>
        <div className="flex flex-col gap-3">
          {WEEKDAY_LABELS.map((label, day) => (
            <div key={label} className="rounded border border-gray-200 p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium">{label}</span>
                <button
                  type="button"
                  onClick={() => addWindow(day)}
                  className="text-xs text-blue-600 hover:underline"
                >
                  + Add window
                </button>
              </div>
              {week[day].length === 0 && <p className="text-xs text-gray-500">Closed all day</p>}
              <div className="flex flex-col gap-2">
                {week[day].map((w) => (
                  <div key={w.key} className="flex items-center gap-2">
                    <input
                      type="time"
                      required
                      value={w.opens}
                      onChange={(event) => updateWindow(day, w.key, 'opens', event.target.value)}
                      className="rounded border border-gray-300 px-2 py-1 text-sm"
                    />
                    <span className="text-xs text-gray-500">to</span>
                    <input
                      type="time"
                      required
                      value={w.closes}
                      onChange={(event) => updateWindow(day, w.key, 'closes', event.target.value)}
                      className="rounded border border-gray-300 px-2 py-1 text-sm"
                    />
                    <button
                      type="button"
                      onClick={() => removeWindow(day, w.key)}
                      className="text-xs text-red-600 hover:underline"
                    >
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {message && (
        <p className={`text-sm ${message.kind === 'error' ? 'text-red-600' : 'text-green-700'}`}>
          {message.text}
        </p>
      )}

      <div>
        <button
          type="submit"
          disabled={saving}
          className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </form>
  );
}
