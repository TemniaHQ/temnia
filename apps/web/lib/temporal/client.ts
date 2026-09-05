import { Client, Connection } from "@temporalio/client";

let clientPromise: Promise<Client> | undefined;

/**
 * One Temporal client per server process. Next.js is a Temporal client only:
 * it starts, queries, and signals workflows and runs none of them.
 */
export function getTemporalClient(): Promise<Client> {
  clientPromise ??= (async () => {
    const address = process.env.TEMPORAL_ADDRESS ?? "localhost:56233";
    const namespace = process.env.TEMPORAL_NAMESPACE ?? "default";
    const connection = await Connection.connect({ address });
    return new Client({ connection, namespace });
  })();
  return clientPromise;
}
