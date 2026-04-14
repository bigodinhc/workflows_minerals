/**
 * Semáforo dependency-free para bounded concurrency.
 * Uso:
 *   const limit = createLimiter(3);
 *   await Promise.all(items.map(i => limit(() => doWork(i))));
 */
export function createLimiter(max) {
    let active = 0;
    const queue = [];

    const next = () => {
        if (active >= max || queue.length === 0) return;
        active++;
        const { fn, resolve, reject } = queue.shift();
        Promise.resolve()
            .then(fn)
            .then(resolve, reject)
            .finally(() => {
                active--;
                next();
            });
    };

    return (fn) => new Promise((resolve, reject) => {
        queue.push({ fn, resolve, reject });
        next();
    });
}
