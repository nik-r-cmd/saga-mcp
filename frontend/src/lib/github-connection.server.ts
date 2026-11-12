function decodeKey(secret: string): Uint8Array {
  const decoded = atob(secret);
  const bytes = new Uint8Array(new ArrayBuffer(decoded.length));
  for (let index = 0; index < decoded.length; index += 1) {
    bytes[index] = decoded.charCodeAt(index);
  }
  return bytes;
}

function encodeBytes(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(new ArrayBuffer(bytes.byteLength));
  copy.set(bytes);
  return copy.buffer;
}

async function cryptoKey(secret: string, usage: KeyUsage[]): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    toArrayBuffer(decodeKey(secret)),
    { name: "AES-GCM" },
    false,
    usage,
  );
}

export async function saveGitHubConnection(
  userId: string,
  connectionAPIKey: string,
  secret: string,
) {
  const iv = crypto.getRandomValues(new Uint8Array(new ArrayBuffer(12)));
  const encrypted = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: "AES-GCM", iv },
      await cryptoKey(secret, ["encrypt"]),
      toArrayBuffer(new TextEncoder().encode(connectionAPIKey)),
    ),
  );
  const ciphertext = encodeBytes(new Uint8Array([...iv, ...encrypted]));
  const { supabaseAdmin } = await import("@/integrations/supabase/client.server");
  const { error } = await supabaseAdmin.from("app_user_connections").upsert(
    {
      user_id: userId,
      connector_id: "github",
      connection_key_ciphertext: ciphertext,
      updated_at: new Date().toISOString(),
    },
    { onConflict: "user_id,connector_id" },
  );
  if (error) throw new Error("GitHub connection could not be saved securely.");
}

export async function getGitHubConnection(userId: string, secret: string): Promise<string | null> {
  const { supabaseAdmin } = await import("@/integrations/supabase/client.server");
  const { data, error } = await supabaseAdmin
    .from("app_user_connections")
    .select("connection_key_ciphertext")
    .eq("user_id", userId)
    .eq("connector_id", "github")
    .maybeSingle();
  if (error) throw new Error("GitHub connection status could not be loaded.");
  if (!data) return null;
  const packed = decodeKey(data.connection_key_ciphertext);
  const iv = toArrayBuffer(packed.slice(0, 12));
  const encrypted = toArrayBuffer(packed.slice(12));
  const decrypted = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv },
    await cryptoKey(secret, ["decrypt"]),
    encrypted,
  );
  return new TextDecoder().decode(decrypted);
}
