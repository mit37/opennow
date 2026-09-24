type EditListingPageProps = {
  params: { id: string };
};

export default function EditListingPage({ params }: EditListingPageProps) {
  return (
    <div>
      <h1 className="mb-2 text-xl font-semibold">Edit listing</h1>
      <p className="text-sm text-gray-600">
        Service id: <code>{params.id}</code>
      </p>
      <p className="mt-4 text-sm text-gray-500">
        Full edit form (hours, closures, status, audit-logged save) is not built yet —
        placeholder for Weekend 3.
      </p>
    </div>
  );
}
