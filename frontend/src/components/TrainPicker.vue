<script setup lang="ts">
import { computed, ref } from 'vue'

import type { Train } from '@/api/types'

const DEPARTURE_LABEL = 'Salida'
// Labels the bot writes into the seats column (SEATS_* in app/bot/renfe.py).
const SEATS_LABEL = 'Plazas'
const SEATS_FREE = 'Disponible'

const props = defineProps<{ trains: Train[] }>()
const emit = defineEmits<{ (e: 'choose', departure: string): void }>()

const selected = ref<string | null>(null)

// Renfe's columns are not known in advance, so they are taken from the rows themselves.
// Seats go right after the time: the table is wider than its panel, and whatever is
// last only shows after scrolling sideways.
const columns = computed(() => {
  const labels = new Set<string>()
  for (const train of props.trains) {
    for (const label of Object.keys(train.cells)) labels.add(label)
  }
  const leading = [DEPARTURE_LABEL, ...(labels.has(SEATS_LABEL) ? [SEATS_LABEL] : [])]
  return [...leading, ...[...labels].filter((label) => !leading.includes(label))]
})

function cellClass(column: string, value: string | undefined) {
  if (column === DEPARTURE_LABEL) return 'picker__time'
  if (column === SEATS_LABEL && value) {
    return value === SEATS_FREE ? 'picker__seats--free' : 'picker__seats--full'
  }
  return undefined
}

function confirm() {
  if (selected.value) emit('choose', selected.value)
}
</script>

<template>
  <form class="picker" @submit.prevent="confirm">
    <div class="picker__scroll">
      <table class="picker__table">
        <thead>
          <tr>
            <th scope="col"><span class="sr-only">Elegir</span></th>
            <th v-for="column in columns" :key="column" scope="col">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="train in trains"
            :key="train.departure"
            :class="{ 'is-selected': selected === train.departure }"
            @click="selected = train.departure"
          >
            <td class="picker__radio">
              <input
                v-model="selected"
                type="radio"
                name="train"
                :value="train.departure"
                :aria-label="`Tren de las ${train.departure}`"
              />
            </td>
            <td
              v-for="column in columns"
              :key="column"
              :class="cellClass(column, train.cells[column])"
            >
              {{ train.cells[column] ?? '—' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <button type="submit" class="btn btn--primary picker__confirm" :disabled="!selected">
      {{ selected ? `Buscar plaza en el de las ${selected}` : 'Elige un tren' }}
    </button>
  </form>
</template>

<style scoped>
.picker {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* A departure board: dark in both themes, monospaced, one train per row. */
.picker__scroll {
  overflow-x: auto;
  /* Native radios follow this, so they match the board instead of the page theme. */
  color-scheme: dark;
  background: var(--board-bg);
  color: var(--board-text);
  border: 1px solid var(--board-line);
  border-radius: var(--radius-sm);
}

.picker__table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font-mono);
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}

.picker__table th {
  padding: 10px 12px;
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--board-muted);
  background: var(--board-raised);
  border-bottom: 1px solid var(--board-line);
  white-space: nowrap;
}

.picker__table td {
  padding: 11px 12px;
  border-bottom: 1px solid var(--board-line);
  white-space: nowrap;
}

.picker__seats--free {
  color: var(--board-ok);
}

.picker__seats--full {
  color: var(--board-muted);
}

.picker__time {
  font-size: 18px;
  font-weight: 600;
  color: var(--board-warn);
}

.picker__table tbody tr {
  cursor: pointer;
  transition: background 0.12s ease;
}

.picker__table tbody tr:last-child td {
  border-bottom: none;
}

.picker__table tbody tr:hover {
  background: var(--board-raised);
}

.picker__table tbody tr.is-selected {
  background: color-mix(in srgb, var(--board-accent) 18%, transparent);
  box-shadow: inset 3px 0 0 var(--board-accent);
}

.picker__radio {
  width: 1%;
}

.picker__radio input {
  accent-color: var(--board-accent);
  width: 16px;
  height: 16px;
  cursor: pointer;
}

.picker__confirm {
  align-self: stretch;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
