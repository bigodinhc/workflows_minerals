import { Readable } from 'node:stream';

import { log } from 'crawlee';
import { google } from 'googleapis';

const FOLDER_MIME = 'application/vnd.google-apps.folder';

let driveClient = null;
const folderCache = new Map(); // path → folderId

function getDrive() {
    if (driveClient) return driveClient;
    let raw = process.env.GOOGLE_CREDENTIALS_JSON;
    if (!raw) throw new Error('GOOGLE_CREDENTIALS_JSON env var is required');
    log.info(`[DEBUG] GOOGLE_CREDENTIALS_JSON length=${raw.length}, first30=${raw.substring(0, 30)}`);
    // Strip wrapping quotes if pasted from a .env file that kept them
    raw = raw.trim();
    if ((raw.startsWith("'") && raw.endsWith("'")) || (raw.startsWith('"') && raw.endsWith('"'))) {
        raw = raw.slice(1, -1);
        log.info(`[DEBUG] Stripped wrapping quotes, now starts with: ${raw.substring(0, 20)}`);
    }
    const creds = JSON.parse(raw);
    log.info(`[DEBUG] Parsed creds OK, type=${creds.type}, email=${creds.client_email}`);
    const auth = new google.auth.GoogleAuth({
        credentials: creds,
        scopes: ['https://www.googleapis.com/auth/drive'],
    });
    driveClient = google.drive({ version: 'v3', auth });
    return driveClient;
}

async function findChildFolder(drive, parentId, name) {
    const q = `'${parentId}' in parents and name='${name.replace(/'/g, "\\'")}' and mimeType='${FOLDER_MIME}' and trashed=false`;
    const res = await drive.files.list({
        q,
        fields: 'files(id, name)',
        spaces: 'drive',
        supportsAllDrives: true,
        includeItemsFromAllDrives: true,
    });
    return res.data.files?.[0]?.id || null;
}

async function ensureSubfolder(drive, parentId, name) {
    const existing = await findChildFolder(drive, parentId, name);
    if (existing) return existing;
    const created = await drive.files.create({
        requestBody: { name, mimeType: FOLDER_MIME, parents: [parentId] },
        fields: 'id',
        supportsAllDrives: true,
    });
    return created.data.id;
}

/**
 * Ensure folder path under rootFolderId exists, return innermost folder ID.
 * `pathParts` is e.g. ['Market Reports', '2026', '04'].
 */
async function ensureFolderPath(drive, rootFolderId, pathParts) {
    const cacheKey = pathParts.join('/');
    if (folderCache.has(cacheKey)) return folderCache.get(cacheKey);
    let parent = rootFolderId;
    for (const part of pathParts) {
        parent = await ensureSubfolder(drive, parent, part);
    }
    folderCache.set(cacheKey, parent);
    return parent;
}

/**
 * Upload `pdfBuffer` to <rootFolderId>/<pathParts...>/<filename>.
 * Returns { fileId, webViewLink }.
 */
export async function uploadPdf(pdfBuffer, { rootFolderId, pathParts, filename }) {
    const drive = getDrive();
    const folderId = await ensureFolderPath(drive, rootFolderId, pathParts);
    const res = await drive.files.create({
        requestBody: { name: filename, parents: [folderId] },
        media: { mimeType: 'application/pdf', body: Readable.from(pdfBuffer) },
        fields: 'id, webViewLink',
        supportsAllDrives: true,
    });
    log.info(`☁️  Uploaded ${filename} to Drive (${res.data.id})`);
    return { fileId: res.data.id, webViewLink: res.data.webViewLink };
}
