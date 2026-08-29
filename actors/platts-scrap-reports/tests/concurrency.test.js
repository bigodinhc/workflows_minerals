import { describe, expect, it } from 'vitest';

import { mapLimit } from '../src/util/concurrency.js';

const tick = () => new Promise((resolve) => { setTimeout(resolve, 1); });

describe('mapLimit', () => {
    it('preserva a ordem dos resultados', async () => {
        const out = await mapLimit([1, 2, 3, 4, 5], 2, async (n) => { await tick(); return n * 2; });
        expect(out).toEqual([2, 4, 6, 8, 10]);
    });

    it('nunca ultrapassa o limite de tarefas simultâneas', async () => {
        let running = 0;
        let peak = 0;
        await mapLimit([1, 2, 3, 4, 5, 6, 7], 3, async () => {
            running += 1;
            peak = Math.max(peak, running);
            await tick();
            running -= 1;
        });
        expect(peak).toBe(3);
    });

    it('lida com lista vazia', async () => {
        expect(await mapLimit([], 3, async () => 1)).toEqual([]);
    });

    it('lida com limite maior que a lista', async () => {
        expect(await mapLimit([1, 2], 10, async (n) => n)).toEqual([1, 2]);
    });

    it('propaga a rejeição da função', async () => {
        await expect(mapLimit([1, 2, 3], 2, async (n) => {
            if (n === 2) throw new Error('estourou no 2');
            return n;
        })).rejects.toThrow('estourou no 2');
    });

    it('passa o índice para a função', async () => {
        expect(await mapLimit(['a', 'b'], 1, async (item, i) => `${i}:${item}`)).toEqual(['0:a', '1:b']);
    });

    it('preserva a ordem dos resultados mesmo quando terminam fora de ordem', async () => {
        const delays = [40, 5, 30, 1, 20];
        const completion = [];
        const out = await mapLimit(delays, 5, async (ms, i) => {
            await new Promise((resolve) => { setTimeout(resolve, ms); });
            completion.push(i);
            return i * 2;
        });
        // guarda o proprio teste: as tarefas de fato terminaram fora de ordem
        expect(completion).not.toEqual([0, 1, 2, 3, 4]);
        expect(out).toEqual([0, 2, 4, 6, 8]);
    });

    it('para de puxar trabalho novo depois de uma rejeicao', async () => {
        const started = [];
        await expect(mapLimit([1, 2, 3, 4, 5, 6], 1, async (n) => {
            started.push(n);
            if (n === 2) throw new Error('estourou no 2');
            return n;
        })).rejects.toThrow('estourou no 2');
        expect(started).toEqual([1, 2]);
    });
});
