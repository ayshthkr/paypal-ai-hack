import InvoiceDetail from "./invoice-detail";

export default async function InvoicePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const values = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (typeof value === "string") values.set(key, value);
    else if (Array.isArray(value)) for (const item of value) values.append(key, item);
  }
  return <InvoiceDetail key={id} id={id} portfolioQuery={values.toString()} />;
}
