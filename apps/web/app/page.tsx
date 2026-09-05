import { HelloForm } from "@/components/hello/hello-form";

export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-8 px-6 py-16">
      <div className="flex max-w-lg flex-col gap-2 text-center">
        <h1 className="font-semibold text-3xl tracking-tight">Temnia</h1>
        <p className="text-muted-foreground">
          The cutting room for the whole episode. S0: the walking skeleton is
          up.
        </p>
      </div>
      <HelloForm />
    </main>
  );
}
