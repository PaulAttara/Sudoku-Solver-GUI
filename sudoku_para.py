import time
from timeit import default_timer as timer

import cupy as cp
import numpy as np

from GUI import print_gui, write_Time, init_GUI


def create_cover(sudoku: np.array, grid_width=9, block_width=3):
    """
    Creates the relationship matrix used by algorithm-x
    :param sudoku: the sudoku matrix (2-d numpy array)
    :param grid_width: number of elements in a row
    :param block_width: number of blocks in a row
    :return: the relationship matrix (called a cover)
    """
    g_len = sudoku.shape[0]  # grid side length
    n_non_zeros = int(np.count_nonzero(sudoku))
    # N_possibilities = #_of_zeros * numbers_per_row + #_of_non_zeros
    n_possibilities = (g_len * g_len - n_non_zeros) * g_len + n_non_zeros
    # There are 4 types of constraints and each type has g_len * g_len constraints
    n_constrains = 4 * g_len * g_len
    # cover stores the 0-1 relationships that Algorithm-X uses
    cover = np.zeros((n_possibilities, n_constrains), dtype=np.int8)
    # possibilities stores the 'name' of a possibility (row, col, number) such that
    # possibilities[i] is the name for row i in cover. This is used to fill in the
    # sudoku at the end
    possibilities = []
    # iterate over each sudoku element, add possibilities and fill in its corresponding
    # relationship with each constraint
    for row in range(g_len):
        for col in range(g_len):
            # if (is a starting number in the sudoku grid)
            if sudoku[row, col] != 0:
                possibility = (row, col, int(sudoku[row, col]))
                add_possibility(
                    possibility,
                    cover,
                    len(possibilities),
                    grid_width=grid_width,
                    block_width=block_width,
                )
                possibilities.append(possibility)
                continue
            # (row,col) is an empty slot, so add all possibilities for this slot
            for n in range(1, grid_width + 1):
                possibility = (row, col, n)
                add_possibility(
                    possibility,
                    cover,
                    len(possibilities),
                    grid_width=grid_width,
                    block_width=block_width,
                )
                possibilities.append(possibility)
    return cover, possibilities


def add_possibility(possibility, cover, cover_row, grid_width=9, block_width=3):
    """
    Adds the possibility and its constraint relationships to the cover matrix.
    :param possibility: The possibility to add
    :param cover: The cover matrix
    :param cover_row: The row in the cover matrix to add the relationships
    :param grid_width: number of elements in a row
    :param block_width: number of blocks in a row
    """
    row, col, n = possibility
    # Row-Column constraint
    cover[cover_row, row * grid_width + col] = 1
    # Row-Number constraint
    cover[cover_row, grid_width * grid_width + row * grid_width + n - 1] = 1
    # Col-Number constraint
    cover[cover_row, 2 * grid_width * grid_width + col * grid_width + n - 1] = 1
    # Block-Number constraint
    # block_idx is the nth block, counting from left to right, top-down
    block_idx = (row // block_width) * grid_width // block_width + (col // block_width)
    cover[cover_row, 3 * grid_width * grid_width + block_idx * grid_width + n - 1] = 1


def solve(cover_gpu, cover_cpu, active_rows, active_cols, solution, solution_path):
    """
    solves the exact cover problem with algorithm-x.
    See https://en.wikipedia.org/wiki/Knuth%27s_Algorithm_X for the high level algorithm
    :param cover_gpu: the cover on the gpu.
    :param cover_cpu: the cover on the cpu.
    :param active_rows: a vector representing which rows haven't and have been removed.
    # this is used for efficiency's sake. Rather than creating a whole new matrix
    # at each iteration when we remove row i, we simply set a active_rows[i] = 0.
    :param active_cols: same as active_rows but for columns.
    :param solution: a list provided which will hold the final solution.
    :param solution_path: a list provided which will hold the execution path of the
    algorithm.
    :return: True if the algorithm successfully found a solution.
    """
    # no active columns means the solution is found.
    if len(active_cols) == 0:
        return True
    # pick the column with the lest number of 1s. This is a heuristic to speed up the
    # algorithm (and is extremely effective for sudoku).
    col, count = min_col(cover_gpu, active_rows, active_cols)
    # a column that doesn't contains 1s means no solution can be found and we backtrack.
    if count == 0:
        return False
    # get all row indices that are active and have a 1 in this column. These rows are
    # candidates for the final solution.
    candidate_rows = np.array(active_rows)[np.flatnonzero(cover_cpu[active_rows, col])]
    for row in candidate_rows:
        solution.append(row)
        solution_path.append((1, row))  # 1 denotes select this row
        # track removed rows and columns so we can easily add them back if we need
        # to backtrack
        rows_to_remove, cols_to_remove = select(
            row, cover_cpu, active_rows, active_cols
        )
        active_rows = np.setdiff1d(np.array(active_rows), rows_to_remove).tolist()
        active_cols = np.setdiff1d(np.array(active_cols), cols_to_remove).tolist()
        solved = solve(
            cover_gpu, cover_cpu, active_rows, active_cols, solution, solution_path
        )
        if solved:
            return True
        # not solved: backtrack
        solution.pop()
        solution_path.append((0, row))  # 1 denotes deselecting this row
        deselect(rows_to_remove, cols_to_remove, active_rows, active_cols)


def select(row, cover, active_rows, active_cols):
    """
    selects a row (possibility) by removing columns and rows that conflict with it.
    :param row: the row representing the selected possibility
    :param cover: the cover matrix
    :param active_rows: active rows in the cover matrix
    :param active_cols: active columns in the cover matrix
    :return: a tuple (removed_rows, removed_cols) which are numpy arrays containing a
    1 if the row/column was removed or 0 otherwise.
    """
    active_rows = np.array(active_rows)
    active_cols = np.array(active_cols)
    columns_to_remove = active_cols[np.nonzero(cover[row, active_cols])[0]]
    rows_to_remove = active_rows[
        np.unique(np.nonzero(cover[np.ix_(active_rows, columns_to_remove)])[0])
    ]
    return rows_to_remove, columns_to_remove


def deselect(removed_rows, removed_cols, active_rows, active_cols):
    """
    restore rows and columns that were removed with select()
    """
    active_rows += removed_rows.tolist()
    active_cols += removed_cols.tolist()


def min_col(cover, active_rows, active_cols):
    """
    :return: (column, count) tuple such that column contains the least number of 1s
    compared to other columns.
    """
    counts = col_counts(cover, active_rows)
    argmin = active_cols[int(cp.argmin(counts[active_cols]))]
    return argmin, counts[argmin]


def col_counts(cover, active_rows):
    return cp.sum(cover[active_rows, :], axis=0)


def print_sudoku(s):
    for i in range(s.shape[0]):
        for j in range(s.shape[1]):
            n = s[i, j]
            if n == 0:
                print("__", end=" ")
            elif n < 10:
                print(f" {n}", end=" ")
            else:
                print(n, end=" ")
        print()
    print()


def solve_sudoku(sudoku: np.array, grid_width=9, block_width=3):
    cover, possibilities = create_cover(
        sudoku, grid_width=grid_width, block_width=block_width
    )
    solution = []
    solution_path = []
    start = timer()
    solved = solve(
        cp.asarray(cover),
        cover,
        list(range(cover.shape[0])),
        list(range(cover.shape[1])),
        solution,
        solution_path,
    )
    solving_time = timer() - start
    if solved:
        completed_sudoku = build_final_sudoku(possibilities, solution, sudoku)
        sudoku_solution_path = build_solving_path(possibilities, solution_path)
        return completed_sudoku, solving_time, sudoku_solution_path
    return None, solving_time, None


def build_solving_path(possibilities, solution_path):
    """
    :return: an (operation_type, row, col, n) tuple list that represents the path
    the algorithm took to solve the algorithm. "ins" means the algorithm inserted
    number "n" into sudoku[row, col]
    """
    sudoku_solution_path = []
    for action, cover_row in solution_path:
        row, col, n = possibilities[cover_row]
        if action == 1:
            sudoku_solution_path.append(("ins", row, col, n))
        else:
            sudoku_solution_path.append(("rem", row, col, n))
    return sudoku_solution_path


def build_final_sudoku(possibilities, solution, sudoku):
    """
    :return: the completed sudoku
    """
    final = np.zeros_like(sudoku)
    for sol in solution:
        row, col, n = possibilities[sol]
        final[row, col] = n
    return final


def main():
    if GUI_enabled == "1":
        init_GUI()
    _sudoku = np.array(
        [
            [0, 5, 0, 0, 7, 0, 0, 8, 3],
            [0, 0, 4, 0, 0, 0, 0, 6, 0],
            [0, 0, 0, 0, 5, 0, 0, 0, 0],
            [8, 3, 0, 6, 0, 0, 0, 0, 0],
            [0, 0, 0, 9, 0, 0, 1, 0, 0],
            [0, 0, 0, 0, 0, 0, 0, 0, 0],
            [5, 0, 7, 0, 0, 0, 3, 0, 0],
            [0, 0, 0, 3, 0, 2, 0, 0, 0],
            [1, 0, 0, 0, 0, 0, 0, 0, 0],
        ]
    )
    _completed_sudoku, _solving_time, _sudoku_solution_path = solve_sudoku(
        _sudoku, grid_width=9, block_width=3
    )
    if _completed_sudoku is None:
        print("No solution found :(")
    else:
        window = None
        for _action, _row, _col, _n in _sudoku_solution_path:
            # os.system("clear")
            if _action == "ins":
                _sudoku[_row, _col] = _n
                window = print_gui(_sudoku) if GUI_enabled == "1" else print_sudoku(_sudoku)
            else:
                _sudoku[_row, _col] = 0
                window = print_gui(_sudoku) if GUI_enabled == "1" else print_sudoku(_sudoku)
            time.sleep(0.1)
        # print_grid(_sudoku)
    if GUI_enabled == "1":
        write_Time(window, str(_solving_time)[:7] + ' seconds')
        time.sleep(6)
    print(f"solved in {_solving_time}")

if __name__ == "__main__":
    while True:
        GUI_enabled = input('For GUI, type 1\nFor CLI, type 2\n')
        if GUI_enabled in ["1","2"]:
            break
    main()
