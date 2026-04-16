import { log } from 'crawlee';

import { getSupabase } from './supabaseClient.js';

const BUCKET = 'platts-reports';

/**
 * Check if a report already exists in the database (dedup).
 * Returns true if the slug+dateKey combo already exists.
 */
export async function isAlreadyStored(slug, dateKey) {
    const sb = getSupabase();
    const { data, error } = await sb
        .from('platts_reports')
        .select('id')
        .eq('slug', slug)
        .eq('date_key', dateKey)
        .limit(1);
    if (error) throw new Error(`Dedup check failed: ${error.message}`);
    return data.length > 0;
}

/**
 * Upload PDF to Supabase Storage and insert metadata into platts_reports.
 * Returns { id, storagePath } on success.
 * Throws on storage or DB error.
 */
export async function uploadPdf(pdfBuffer, { storagePath, metadata }) {
    const sb = getSupabase();

    // 1. Upload to storage bucket
    const { error: storageError } = await sb.storage
        .from(BUCKET)
        .upload(storagePath, pdfBuffer, {
            contentType: 'application/pdf',
            upsert: true,
        });
    if (storageError) {
        throw new Error(`Storage upload failed: ${storageError.message}`);
    }
    log.info(`Stored ${storagePath} in bucket ${BUCKET}`);

    // 2. Insert metadata into platts_reports
    const row = {
        slug: metadata.slug,
        date_key: metadata.dateKey,
        report_name: metadata.reportName,
        report_type: metadata.reportType,
        frequency: metadata.frequency || null,
        cover_date: metadata.coverDate || null,
        published_date: metadata.publishedDate || null,
        storage_path: storagePath,
        file_size_bytes: pdfBuffer.length,
    };
    const { data, error: dbError } = await sb
        .from('platts_reports')
        .insert(row)
        .select('id')
        .single();
    if (dbError) {
        throw new Error(`DB insert failed: ${dbError.message}`);
    }
    log.info(`Inserted platts_reports row: ${data.id}`);
    return { id: data.id, storagePath };
}

/**
 * Update the telegram_message_id for a report after sending the summary.
 */
export async function setTelegramMessageId(reportId, messageId) {
    const sb = getSupabase();
    const { error } = await sb
        .from('platts_reports')
        .update({ telegram_message_id: messageId })
        .eq('id', reportId);
    if (error) {
        log.warning(`Failed to update telegram_message_id for ${reportId}: ${error.message}`);
    }
}
