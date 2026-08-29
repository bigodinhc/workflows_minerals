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

    const worker = async () => {
        for (;;) {
            const index = cursor;
            cursor += 1;
            if (index >= items.length) return;
            results[index] = await fn(items[index], index);
        }
    };

    const size = Math.max(1, Math.min(limit, items.length));
    await Promise.all(Array.from({ length: size }, worker));
    return results;
}
