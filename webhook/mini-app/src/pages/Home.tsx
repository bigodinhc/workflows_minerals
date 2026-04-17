export default function Home({
  onNavigate: _,
}: {
  onNavigate: (
    page: string,
    params?: Record<string, string>,
  ) => void;
}) {
  return <div className="p-4 text-text-secondary">Home loading...</div>;
}
