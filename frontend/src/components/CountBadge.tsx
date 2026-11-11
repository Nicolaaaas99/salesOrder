/** Small red count bubble (e.g. orders waiting for review). Hidden at 0. */
export function CountBadge({ count, title }: { count: number; title?: string }) {
  if (count <= 0) return null;
  return (
    <span
      className="ml-1.5 inline-flex min-w-[1.125rem] h-[1.125rem] items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-bold leading-none text-white align-middle"
      title={title ? `${count} ${title}` : undefined}
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}
