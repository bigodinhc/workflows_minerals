/**
 * Executa `fn` sobre `items` com no máximo `limit` tarefas simultâneas,
 * preservando a ordem dos resultados.
 *
 * O backfill baixa centenas de PDFs; sem limite, dispararíamos todos de uma
 * vez contra o Platts. Com limite baixo, o trabalho leva dezenas de minutos e
 * ninguém se irrita.
 */
export async function mapLimit(items, limit, fn) {
    const results = new Array(items.length);
    let cursor = 0;
    let aborted = false;

    const worker = async () => {
        for (;;) {
            if (aborted) return;
            const index = cursor;
            cursor += 1;
            if (index >= items.length) return;
            try {
                results[index] = await fn(items[index], index);
            } catch (error) {
                // Para de puxar trabalho novo: sem isto, os demais workers
                // seguem chamando a API depois que o chamador ja desistiu.
                aborted = true;
                throw error;
            }
        }
    };

    const size = Math.max(1, Math.min(limit, items.length));
    await Promise.all(Array.from({ length: size }, worker));
    return results;
}
