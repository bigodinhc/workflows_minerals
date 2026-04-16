import { Readable } from 'node:stream';

import { log } from 'crawlee';
import { google } from 'googleapis';

const FOLDER_MIME = 'application/vnd.google-apps.folder';

let driveClient = null;
const folderCache = new Map();

/**
 * Build a Google Drive client. Supports two auth modes:
 *
 *   1. OAuth2 (personal Drive) — set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET,
 *      GOOGLE_OAUTH_REFRESH_TOKEN. Files are owned by the authorizing user.
 *
 *   2. Service Account (Shared Drive / Workspace) — set GOOGLE_CREDENTIALS_JSON.
 *      Only works with Shared Drives (service accounts have 0 quota on personal Drive).
 */
function getDrive() {
    if (driveClient) return driveClient;

    const clientId = process.env.GOOGLE_OAUTH_CLIENT_ID;
    const clientSecret = process.env.GOOGLE_OAUTH_CLIENT_SECRET;
    const refreshToken = process.env.GOOGLE_OAUTH_REFRESH_TOKEN;

    if (clientId && clientSecret && refreshToken) {
        log.info('Drive auth: OAuth2 (personal account)');
        const oauth2 = new google.auth.OAuth2(clientId, clientSecret);
        oauth2.setCredentials({ refresh_token: refreshToken });
        driveClient = google.drive({ version: 'v3', auth: oauth2 });
        return driveClient;
    }

    let raw = process.env.GOOGLE_CREDENTIALS_JSON;
    if (raw) {
        log.info('Drive auth: Service Account (Shared Drive only)');
        raw = raw.trim();
        if ((raw.startsWith("'") && raw.endsWith("'")) || (raw.startsWith('"') && raw.endsWith('"'))) {
            raw = raw.slice(1, -1);
        }
        const creds = JSON.parse(raw);
        const auth = new google.auth.GoogleAuth({
            credentials: creds,
            scopes: ['https://www.googleapis.com/auth/drive'],
        });
        driveClient = google.drive({ version: 'v3', auth });
        return driveClient;
    }

    throw new Error(
        'Drive auth not configured. Set GOOGLE_OAUTH_CLIENT_ID + GOOGLE_OAUTH_CLIENT_SECRET + GOOGLE_OAUTH_REFRESH_TOKEN (personal Drive) ' +
        'or GOOGLE_CREDENTIALS_JSON (Shared Drive).',
    );
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
    log.info(`Uploaded ${filename} to Drive (${res.data.id})`);
    return { fileId: res.data.id, webViewLink: res.data.webViewLink };
}
