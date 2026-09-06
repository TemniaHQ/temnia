import { S3Client } from "@aws-sdk/client-s3";

export interface StorageSettings {
  bucket: string;
  endpoint: string;
  publicEndpoint: string;
  region: string;
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}

export function storageSettings(): StorageSettings {
  const endpoint = required("STORAGE_ENDPOINT");
  return {
    bucket: required("STORAGE_BUCKET"),
    endpoint,
    publicEndpoint: process.env.STORAGE_PUBLIC_ENDPOINT ?? endpoint,
    region: process.env.STORAGE_REGION ?? "auto",
  };
}

function makeClient(endpoint: string): S3Client {
  return new S3Client({
    credentials: {
      accessKeyId: required("STORAGE_ACCESS_KEY_ID"),
      secretAccessKey: required("STORAGE_SECRET_ACCESS_KEY"),
    },
    endpoint,
    forcePathStyle: true,
    region: storageSettings().region,
    // Garage and R2 reject the SDK's default CRC32 upload checksums
    // (x-amz-sdk-checksum-algorithm); only compute what the store requires.
    requestChecksumCalculation: "WHEN_REQUIRED",
    responseChecksumValidation: "WHEN_REQUIRED",
  });
}

let serverClient: S3Client | undefined;
let signingClient: S3Client | undefined;

/** Talks to the store from the server (proxy reads, multipart control). */
export function storage(): S3Client {
  serverClient ??= makeClient(storageSettings().endpoint);
  return serverClient;
}

/**
 * Signs URLs the browser will use. Same credentials, but the endpoint the
 * browser can reach (identical in compose; the public R2 endpoint on staging).
 */
export function browserStorage(): S3Client {
  signingClient ??= makeClient(storageSettings().publicEndpoint);
  return signingClient;
}
