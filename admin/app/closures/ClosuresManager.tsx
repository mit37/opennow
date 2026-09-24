'use client';

import { useEffect, useState } from 'react';
import { supabase } from '@/lib/supabaseClient';

export type ServiceOption = {
  id: string;
  name: string;
};

type ExceptionRow = {
  id: string;
  date: string;
  closed: boolean;
  opens: string | null;
  closes: string | null;
  reason: string | null;
};

type ClosuresManagerProps = {
  services: ServiceOption[];
};

type Message = { kind: 'success' | 'error'; text: string } | null;

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function ClosuresManager({ services }: ClosuresManagerProps) {
  const [serviceId, setServiceId] = useState(services[0]?.id ?? '');
  const [exceptions, setExceptions] = useState<ExceptionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<Message>(null);

  const [date, setDate] = useState(todayIso());
  const [closed, setClosed] = useState(true);
  const [opens, setOpens] = useState('');
  const [closes, setCloses] = useState('');
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function loadExceptions(forServiceId: string) {
    if (!forServiceId) {
      setExceptions([]);
      return;
    }
    setLoading(true);
    const { data, error } = await supabase
      .from('schedule_exception')
      .select('id, date, closed, opens, closes, reason')
      .eq('service_id', forServiceId)
      .gte('date', todayIso())
      .order('date');

    setLoading(false);
    if (error) {
      setMessage({ kind: 'error', text: `Failed to load closures: ${error.message}` });
      setExceptions([]);
      return;
    }
    setExceptions((data as ExceptionRow[]) ?? []);
  }

  useEffect(() => {
    loadExceptions(serviceId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serviceId]);

  function resetForm() {
    setDate(todayIso());
    setClosed(true);
    setOpens('');
    setCloses('');
    setReason('');
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);

    if (!serviceId) {
      setMessage({ kind: 'error', text: 'Choose a service first.' });
      return;
    }
    if (!closed) {
      if (!opens || !closes) {
        setMessage({ kind: 'error', text: 'Give an opens and closes time, or check "Fully closed".' });
        return;
      }
      if (opens >= closes) {
        setMessage({ kind: 'error', text: 'Opens time must be before closes time.' });
        return;
      }
    }

    setSubmitting(true);
    const { error } = await supabase.from('schedule_exception').insert({
      service_id: serviceId,
      date,
      closed,
      opens: closed ? null : opens,
      closes: closed ? null : closes,
      reason: reason.trim() === '' ? null : reason.trim(),
    });
    setSubmitting(false);

    if (error) {
      setMessage({ kind: 'error', text: `Failed to add closure: ${error.message}` });
      return;
    }

    setMessage({ kind: 'success', text: 'Closure added. Changes are live now.' });
    resetForm();
    await loadExceptions(serviceId);
  }

  return (
    <div className="flex flex-col gap-6">
      <label className="flex max-w-sm flex-col text-sm">
        Service
        <select
          value={serviceId}
          onChange={(event) => setServiceId(event.target.value)}
          className="mt-1 rounded border border-gray-300 px-3 py-2"
        >
          {services.length === 0 && <option value="">No services available</option>}
          {services.map((service) => (
            <option key={service.id} value={service.id}>
              {service.name}
            </option>
          ))}
        </select>
      </label>

      <div>
        <h2 className="mb-2 text-sm font-semibold">Upcoming closures</h2>
        {loading && <p className="text-sm text-gray-500">Loading…</p>}
        {!loading && (
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left">
                <th className="py-2 pr-4">Date</th>
                <th className="py-2 pr-4">Hours</th>
                <th className="py-2 pr-4">Reason</th>
              </tr>
            </thead>
            <tbody>
              {exceptions.map((row) => (
                <tr key={row.id} className="border-b border-gray-100">
                  <td className="py-2 pr-4">{row.date}</td>
                  <td className="py-2 pr-4">
                    {row.closed
                      ? 'Closed'
                      : `${(row.opens ?? '').slice(0, 5)}–${(row.closes ?? '').slice(0, 5)}`}
                  </td>
                  <td className="py-2 pr-4">{row.reason ?? '—'}</td>
                </tr>
              ))}
              {exceptions.length === 0 && (
                <tr>
                  <td colSpan={3} className="py-4 text-gray-500">
                    No upcoming closures.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">Add a closure</h2>
        <form onSubmit={handleSubmit} className="flex max-w-sm flex-col gap-3">
          <label className="flex flex-col text-sm">
            Date
            <input
              type="date"
              required
              value={date}
              min={todayIso()}
              onChange={(event) => setDate(event.target.value)}
              className="mt-1 rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={closed}
              onChange={(event) => setClosed(event.target.checked)}
            />
            Fully closed
          </label>
          {!closed && (
            <div className="flex items-center gap-2">
              <input
                type="time"
                required={!closed}
                value={opens}
                onChange={(event) => setOpens(event.target.value)}
                className="rounded border border-gray-300 px-2 py-1 text-sm"
              />
              <span className="text-xs text-gray-500">to</span>
              <input
                type="time"
                required={!closed}
                value={closes}
                onChange={(event) => setCloses(event.target.value)}
                className="rounded border border-gray-300 px-2 py-1 text-sm"
              />
            </div>
          )}
          <label className="flex flex-col text-sm">
            Reason
            <input
              type="text"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="e.g. holiday, staff shortage"
              className="mt-1 rounded border border-gray-300 px-3 py-2"
            />
          </label>
          {message && (
            <p className={`text-sm ${message.kind === 'error' ? 'text-red-600' : 'text-green-700'}`}>
              {message.text}
            </p>
          )}
          <div>
            <button
              type="submit"
              disabled={submitting || !serviceId}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {submitting ? 'Saving…' : 'Add closure'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
