import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState
} from "@whiskeysockets/baileys";
import P from "pino";
import qrcode from "qrcode-terminal";
import fs from "fs/promises";
import path from "path";

const BACKEND_URL = (process.env.BACKEND_URL || "").replace(/\/+$/, "");
const WA_BRIDGE_TOKEN = process.env.WA_BRIDGE_TOKEN || "";
const ACCOUNT_PHONE = (process.env.ACCOUNT_PHONE || "").trim();
const AUTH_DIR = process.env.WA_AUTH_DIR || "/data/wa_auth";
const GROUP_ALLOWLIST = (process.env.GROUP_ALLOWLIST || "")
  .split("|")
  .map(x => x.trim())
  .filter(Boolean);
const SEND_OLD_MESSAGES = /^(1|true|yes|on)$/i.test(process.env.SEND_OLD_MESSAGES || "false");

if (!BACKEND_URL) throw new Error("BACKEND_URL is required");
if (!WA_BRIDGE_TOKEN) throw new Error("WA_BRIDGE_TOKEN is required");
if (!ACCOUNT_PHONE) throw new Error("ACCOUNT_PHONE is required");

await fs.mkdir(AUTH_DIR, { recursive: true });

const logger = P({ level: process.env.LOG_LEVEL || "info" });
const recentIds = new Map();

function remember(id) {
  const now = Date.now();
  recentIds.set(id, now);
  for (const [k, ts] of recentIds) {
    if (now - ts > 6 * 60 * 60 * 1000) recentIds.delete(k);
  }
}

function alreadySeen(id) {
  return recentIds.has(id);
}

function getText(message) {
  if (!message) return "";
  if (message.conversation) return message.conversation;
  if (message.extendedTextMessage?.text) return message.extendedTextMessage.text;
  if (message.imageMessage?.caption) return message.imageMessage.caption;
  if (message.videoMessage?.caption) return message.videoMessage.caption;
  if (message.documentMessage?.caption) return message.documentMessage.caption;
  if (message.buttonsResponseMessage?.selectedDisplayText)
    return message.buttonsResponseMessage.selectedDisplayText;
  if (message.listResponseMessage?.title)
    return message.listResponseMessage.title;
  return "";
}

function normalizePhone(jid) {
  if (!jid) return "";
  return String(jid).split("@")[0].split(":")[0].replace(/\D/g, "");
}

async function postWithRetry(payload) {
  let lastError = null;
  for (let attempt = 1; attempt <= 5; attempt++) {
    try {
      const res = await fetch(`${BACKEND_URL}/whatsapp-live/api/ingest`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-bridge-token": WA_BRIDGE_TOKEN
        },
        body: JSON.stringify(payload)
      });

      const body = await res.text();
      if (res.ok) {
        logger.info(
          { group: payload.group_name, messageId: payload.external_message_id },
          "Message delivered to Alliance app"
        );
        return;
      }

      if (res.status === 403) {
        logger.warn(
          { group: payload.group_name, response: body },
          "Group is not active in WhatsApp Sources. Message skipped."
        );
        return;
      }

      throw new Error(`Backend ${res.status}: ${body}`);
    } catch (err) {
      lastError = err;
      logger.warn({ err: String(err), attempt }, "Backend delivery failed");
      await new Promise(r => setTimeout(r, Math.min(30000, 1000 * 2 ** attempt)));
    }
  }
  logger.error({ err: String(lastError), payload }, "Message delivery permanently failed");
}

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: state,
    logger,
    browser: ["Alliance Property Intelligence", "Chrome", "1.0.0"],
    syncFullHistory: SEND_OLD_MESSAGES,
    markOnlineOnConnect: false,
    generateHighQualityLinkPreview: false
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async update => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log("\nSCAN THIS QR WITH THE DEDICATED WHATSAPP NUMBER:\n");
      qrcode.generate(qr, { small: true });
      console.log("\nWhatsApp > Linked devices > Link a device\n");
    }

    if (connection === "open") {
      logger.info("WhatsApp linked. Live group capture is ON.");
    }

    if (connection === "close") {
      const statusCode =
        lastDisconnect?.error?.output?.statusCode ||
        lastDisconnect?.error?.statusCode ||
        0;
      const loggedOut = statusCode === DisconnectReason.loggedOut;

      logger.warn({ statusCode, loggedOut }, "WhatsApp connection closed");

      if (loggedOut) {
        logger.error(
          "Session was logged out. Delete the auth volume contents and scan QR again."
        );
        process.exit(1);
      }

      setTimeout(start, 3000);
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify" && !SEND_OLD_MESSAGES) return;

    for (const msg of messages) {
      try {
        const remoteJid = msg?.key?.remoteJid || "";
        if (!remoteJid.endsWith("@g.us")) continue;
        if (msg.key.fromMe) continue;

        const text = getText(msg.message).trim();
        if (!text) continue;

        const externalId = msg.key.id || "";
        if (!externalId || alreadySeen(externalId)) continue;
        remember(externalId);

        let meta;
        try {
          meta = await sock.groupMetadata(remoteJid);
        } catch {
          meta = null;
        }

        const groupName = (meta?.subject || remoteJid).trim();
        if (GROUP_ALLOWLIST.length && !GROUP_ALLOWLIST.includes(groupName)) {
          continue;
        }

        const participantJid =
          msg.key.participant ||
          msg.participant ||
          msg.message?.extendedTextMessage?.contextInfo?.participant ||
          "";

        const senderPhone = normalizePhone(participantJid);
        const senderName =
          msg.pushName ||
          senderPhone ||
          "Unknown";

        const epoch = Number(msg.messageTimestamp || 0);
        const timestamp = epoch
          ? new Date(epoch * 1000).toISOString()
          : new Date().toISOString();

        await postWithRetry({
          account_phone: ACCOUNT_PHONE,
          group_name: groupName,
          external_message_id: externalId,
          sender_name: senderName,
          sender_phone: senderPhone,
          timestamp,
          text
        });
      } catch (err) {
        logger.error({ err: String(err) }, "Could not process incoming group message");
      }
    }
  });
}

start().catch(err => {
  logger.fatal({ err: String(err) }, "Worker failed to start");
  process.exit(1);
});
