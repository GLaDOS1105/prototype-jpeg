import collections
import itertools
import functools
import operator

from bidict import bidict
import numpy as np


EOB = (0, 0)
ZRL = (15, 0)
DC = 'DC'
AC = 'AC'
LUMINANCE = 'luminance'
CHROMINANCE = 'chrominance'


class Encoder:
    def __init__(self, data):
        # Append 'cr' to 'cb' to make them as 'luminance'. Thus, `self.data`
        # would be:
        # dict {
        #   LUMINANCE: (y),
        #   CHROMINANCE: (cb)(cr)
        # }
        self.data = {
            LUMINANCE: data['y'],
            CHROMINANCE: np.vstack((data['cb'], data['cr']))
        }

        # The differential DC in blocks order: 'y', 'cb', 'cr'.
        self._diff_dc = None

        # The run-length-encoded AC in blocks order: 'y', 'cb', 'cr'. Every
        # sublist is the run-length-encoded AC of one block.
        self._run_length_ac = None

    @property
    def diff_dc(self):
        if self._diff_dc is None:
            self._get_diff_dc()
        return self._diff_dc

    @diff_dc.setter
    def diff_dc(self, value):
        self._diff_dc = value

    @property
    def run_length_ac(self):
        if self._run_length_ac is None:
            self._get_run_length_ac()
        return self._run_length_ac

    @run_length_ac.setter
    def run_length_ac(self, value):
        self._run_length_ac = value

    def encode(self):
        """Encode differential DC and run-length-encoded AC with baseline JPEG
        Huffman table.

        Returns:
            dict -- A dictionary containing DC and AC with luminance and
                chrominance encoded layers. The format is:
                ```
                ret = {
                    DC: {LUMINANCE: '01...', CHROMINANCE: '01...'},
                    AC: {LUMINANCE: '01...', CHROMINANCE: '01...'}
                }
                ```
        """

        ret = {DC: {}, AC: {}}
        for layer_type in (LUMINANCE, CHROMINANCE):
            ret[DC][layer_type] = ''.join(encode_huffman(v, layer_type)
                                          for v in self.diff_dc[layer_type])
            ret[AC][layer_type] = ''.join(
                encode_huffman(v, layer_type)
                for v in self.run_length_ac[layer_type]
            )
        return ret

    def _get_diff_dc(self):
        """Calculate the differential DC in the following format.
        self._diff_dc = {
            LUMINANCE: (tuple of integers),
            CHROMINANCE: (tuple of intergers)
        }
        """

        self._diff_dc = {k: encode_differential(l[:, 0, 0])
                         for k, l in self.data.items()}

    def _get_run_length_ac(self):
        """Calculate the run-length-encoded AC in the following format.
        self._run_length_ac = {
            LUMINANCE: (list of integers),
            CHROMINANCE: (list of integers)
        }
        """

        self._run_length_ac = {}
        for key, layer in self.data.items():
            seq = []
            for block in layer:
                seq.extend(encode_run_length(list(iter_zig_zag(block))[1:]))
            self._run_length_ac[key] = seq


class Decoder:
    def __init__(self, data):
        """Create a decoder based on baseline JPEG Huffman table.

        Arguments:
            data {dict} -- A dictionary containing DC/AC luminance and
                chrominance bit array as following format.
                {
                    DC: {LUMINANCE: '.01..', CHROMINANCE: '.01..'},
                    AC: {LUMINANCE: '.01..', CHROMINANCE: '.01..'}
                }
        """

        self.data = data

        # A dictionary containing all DC of blocks.
        self._dc = None
        # A dictionary with nested 2D list containing all AC of blocks without
        # zig-zag iteration.
        self._ac = None

    def decode(self):
        if len(self.dc[CHROMINANCE]) % 2 or len(self.ac[CHROMINANCE]) % 2:
            raise ValueError('The length of DC chrominance '
                             f'{len(self.dc[CHROMINANCE])} or AC chrominance '
                             f'{len(self.ac[CHROMINANCE])} cannot be divided '
                             'by 2 evenly to seperate into Cb and Cr.')

        shaped = {}
        for layer in (LUMINANCE, CHROMINANCE):
            if len(self.dc[layer]) != len(self.ac[layer]):
                raise ValueError(f'DC {layer} size {len(self.dc[layer])} is not'
                                 f' equal to AC {layer} size '
                                 f'{len(self.ac[layer])}.')

            shaped[layer] = np.array(tuple(
                inverse_iter_zig_zag((dc, ) + ac, size=8)
                for dc, ac in zip(self.dc[layer], self.ac[layer])
            ))

        cb, cr = np.split(shaped[CHROMINANCE], 2)

        return collections.OrderedDict((
            ('y', shaped[LUMINANCE]),
            ('cb', cb),
            ('cr', cr),
        ))

    @property
    def dc(self):
        if self._dc is None:
            self._get_dc()
        return self._dc

    @property
    def ac(self):
        if self._ac is None:
            self._get_ac()
        return self._ac

    def _get_dc(self):
        self._dc = {
            layer: decode_differential(decode_huffman(value, DC, layer))
            for layer, value in self.data[DC].items()
        }

    def _get_ac(self):
        def isplit(iterable, splitter):
            ret = []
            for item in iterable:
                ret.append(item)
                if item == splitter:
                    yield ret
                    ret = []

        self._ac = {}
        for layer, value in self.data[AC].items():
            self._ac[layer] = tuple(
                decode_run_length(pairs)
                for pairs in isplit(decode_huffman(value, AC, layer), EOB)
            )


def encode_huffman(value, layer_type):
    """Encode the Huffman coding of value.

    Arguments:
        value {int or tuple} -- Differential DC (int) or run-length AC (tuple).
        layer_type {LUMINANCE or CHROMINANCE} -- Specify the table of certain
            layer.

    Raises:
        ValueError -- When the value is out of the range.

    Returns:
        str -- Huffman encoded bit array.
    """

    def index_2d(table, target):
        for i, row in enumerate(table):
            for j, element in enumerate(row):
                if target == element:
                    return (i, j)
        raise ValueError('Cannot find the target value in the table.')

    if not isinstance(value, collections.Iterable):  # DC
        if value <= -2048 or value >= 2048:
            raise ValueError(
                f'Differential DC {value} should be within [-2047, 2047].'
            )

        size, fixed_code_idx = index_2d(HUFFMAN_CATEGORIES, value)

        if size == 0:
            return HUFFMAN_CATEGORY_CODEWORD[DC][layer_type][size]
        return (HUFFMAN_CATEGORY_CODEWORD[DC][layer_type][size]
                + '{:0{padding}b}'.format(fixed_code_idx, padding=size))
    else:   # AC
        value = tuple(value)
        if value == EOB or value == ZRL:
            return HUFFMAN_CATEGORY_CODEWORD[AC][layer_type][value]

        run, nonzero = value
        if nonzero == 0 or nonzero <= -1024 or nonzero >= 1024:
            raise ValueError(
                f'AC coefficient nonzero {value} should be within [-1023, 0) '
                'or (0, 1023].'
            )

        size, fixed_code_idx = index_2d(HUFFMAN_CATEGORIES, nonzero)
        return (HUFFMAN_CATEGORY_CODEWORD[AC][layer_type][(run, size)]
                + '{:0{padding}b}'.format(fixed_code_idx, padding=size))


def decode_huffman(bit_seq, dc_ac, layer_type):
    """Decode a bit sequence encoded by JPEG baseline Huffman table.

    Arguments:
        bit_seq {str} -- The encoded bit sequence.
        dc_ac {DC or AC} -- The type of current.
        layer_type {LUMINANCE or CHROMINANCE} -- The type of color space.

    Raises:
        IndexError -- When there is not enough bits in bit sequence to decode
            DIFF value codeword.
        KeyError -- When not able to find any prefix in current slice of bit
            sequence in Huffman table.

    Returns:
        Generator -- A generator and its item is decoded value which could be an
            integer (differential DC) or a tuple (run-length-encoded AC).
    """

    def diff_value(idx, size):
        if idx >= bit_len or idx + size > bit_len:
            raise IndexError('There is not enough bits to '
                             'decode DIFF value codeword.')
        fixed = bit_seq[idx:idx + size]
        return int(fixed, 2)

    bit_len = len(bit_seq)
    current_idx = 0
    while current_idx < bit_len:
        #   1. Consume next 16 bits as `current_slice`.
        #   2. Try to find the `current_slice` in Huffman table.
        #   3. If found, yield the corresponding key and go to step 4.
        #      Otherwise, remove the last element in `current_slice` and go to
        #      step 2.
        #   4. Consume next n bits, where n is the category (size) in returned
        #      key yielded in step 3. Use those info to decode the data.
        remaining_len = bit_len - current_idx
        current_slice = bit_seq[
            current_idx:
            current_idx + (16 if remaining_len > 16 else remaining_len)
        ]
        err_cache = current_slice
        while current_slice:
            if (current_slice in
                    HUFFMAN_CATEGORY_CODEWORD[dc_ac][layer_type].inv):
                key = (HUFFMAN_CATEGORY_CODEWORD[dc_ac][layer_type]
                       .inv[current_slice])
                if dc_ac == DC:
                    size = key
                    if size == 0:
                        yield 0
                    else:
                        yield HUFFMAN_CATEGORIES[size][diff_value(
                            current_idx + len(current_slice),
                            size
                        )]
                else:  # AC
                    run, size = key
                    if key == EOB or key == ZRL:
                        yield key
                    else:
                        yield (run, HUFFMAN_CATEGORIES[size][diff_value(
                            current_idx + len(current_slice),
                            size
                        )])

                current_idx += len(current_slice) + size
                break
            else:
                current_slice = current_slice[:-1]
        else:
            raise KeyError(
                f'Cannot find any prefix of {err_cache} in Huffman table.'
            )


def encode_differential(seq):
    return tuple(
        (item - seq[idx - 1]) if idx else item
        for idx, item in enumerate(seq)
    )


def decode_differential(seq):
    return tuple(itertools.accumulate(seq))


def encode_run_length(seq):
    groups = [(len(tuple(group)), key)
              for key, group in itertools.groupby(seq)]
    ret = []
    borrow = False  # Borrow one pair in the next group whose key is nonzero.
    if groups[-1][1] == 0:
        del groups[-1]
    for idx, (length, key) in enumerate(groups):
        if borrow == True:
            length -= 1
            borrow = False
        if length == 0:
            continue
        if key == 0:
            # Deal with the case run (0s) more than 16 --> ZRL.
            while length >= 16:
                ret.append(ZRL)
                length -= 16
            ret.append((length, groups[idx + 1][1]))
            borrow = True
        else:
            ret.extend(((0, key), ) * length)
    return ret + [EOB]


def decode_run_length(seq):
    # Remove the last element as the last created by EOB would always be a `0`.
    return tuple(item for l, k in seq for item in [0] * l + [k])[:-1]


def iter_zig_zag(data):
    if data.shape[0] != data.shape[1]:
        raise ValueError('The shape of input array should be square.')
    x, y = 0, 0
    for _ in np.nditer(data):
        yield data[y][x]
        if (x + y) % 2 == 1:
            x, y = move_zig_zag_idx(x, y, data.shape[0])
        else:
            y, x = move_zig_zag_idx(y, x, data.shape[0])


def inverse_iter_zig_zag(seq, size=None, fill=0):
    def smallest_square_larger_than(value):
        for ret in itertools.count():
            if ret**2 >= value:
                return ret

    if size is None:
        size = smallest_square_larger_than(len(seq))
    seq = tuple(seq) + (fill, ) * (size**2 - len(seq))
    ret = np.empty((size, size), dtype=int)
    x, y = 0, 0
    for value in seq:
        ret[y][x] = value
        if (x + y) % 2 == 1:
            x, y = move_zig_zag_idx(x, y, size)
        else:
            y, x = move_zig_zag_idx(y, x, size)
    return ret


def move_zig_zag_idx(i, j, size):
    if j < (size - 1):
        return (max(0, i - 1), j + 1)
    return (i + 1, j)


HUFFMAN_CATEGORIES = (
    (0, ),
    (-1, 1),
    (-3, -2, 2, 3),
    (*range(-7, -4 + 1), *range(4, 7 + 1)),
    (*range(-15, -8 + 1), *range(8, 15 + 1)),
    (*range(-31, -16 + 1), *range(16, 31 + 1)),
    (*range(-63, -32 + 1), *range(32, 63 + 1)),
    (*range(-127, -64 + 1), *range(64, 127 + 1)),
    (*range(-255, -128 + 1), *range(128, 255 + 1)),
    (*range(-511, -256 + 1), *range(256, 511 + 1)),
    (*range(-1023, -512 + 1), *range(512, 1023 + 1)),
    (*range(-2047, -1024 + 1), *range(1024, 2047 + 1)),
    (*range(-4095, -2048 + 1), *range(2048, 4095 + 1)),
    (*range(-8191, -4096 + 1), *range(4096, 8191 + 1)),
    (*range(-16383, -8192 + 1), *range(8192, 16383 + 1)),
    (*range(-32767, -16384 + 1), *range(16384, 32767 + 1))
)

HUFFMAN_CATEGORY_CODEWORD = {
    DC: {
        LUMINANCE: bidict({
            0:  '00',
            1:  '010',
            2:  '011',
            3:  '100',
            4:  '101',
            5:  '110',
            6:  '1110',
            7:  '11110',
            8:  '111110',
            9:  '1111110',
            10: '11111110',
            11: '111111110'
        }),
        CHROMINANCE: bidict({
            0:  '00',
            1:  '01',
            2:  '10',
            3:  '110',
            4:  '1110',
            5:  '11110',
            6:  '111110',
            7:  '1111110',
            8:  '11111110',
            9:  '111111110',
            10: '1111111110',
            11: '11111111110'
        })
    },
    AC: {
        LUMINANCE: bidict({
            EOB: '1010',  # (0, 0)
            ZRL: '11111111001',  # (F, 0)

            (0, 1):  '00',
            (0, 2):  '01',
            (0, 3):  '100',
            (0, 4):  '1011',
            (0, 5):  '11010',
            (0, 6):  '1111000',
            (0, 7):  '11111000',
            (0, 8):  '1111110110',
            (0, 9):  '1111111110000010',
            (0, 10): '1111111110000011',

            (1, 1):  '1100',
            (1, 2):  '11011',
            (1, 3):  '1111001',
            (1, 4):  '111110110',
            (1, 5):  '11111110110',
            (1, 6):  '1111111110000100',
            (1, 7):  '1111111110000101',
            (1, 8):  '1111111110000110',
            (1, 9):  '1111111110000111',
            (1, 10): '1111111110001000',

            (2, 1):  '11100',
            (2, 2):  '11111001',
            (2, 3):  '1111110111',
            (2, 4):  '111111110100',
            (2, 5):  '1111111110001001',
            (2, 6):  '1111111110001010',
            (2, 7):  '1111111110001011',
            (2, 8):  '1111111110001100',
            (2, 9):  '1111111110001101',
            (2, 10): '1111111110001110',

            (3, 1):  '111010',
            (3, 2):  '111110111',
            (3, 3):  '111111110101',
            (3, 4):  '1111111110001111',
            (3, 5):  '1111111110010000',
            (3, 6):  '1111111110010001',
            (3, 7):  '1111111110010010',
            (3, 8):  '1111111110010011',
            (3, 9):  '1111111110010100',
            (3, 10): '1111111110010101',

            (4, 1):  '111011',
            (4, 2):  '1111111000',
            (4, 3):  '1111111110010110',
            (4, 4):  '1111111110010111',
            (4, 5):  '1111111110011000',
            (4, 6):  '1111111110011001',
            (4, 7):  '1111111110011010',
            (4, 8):  '1111111110011011',
            (4, 9):  '1111111110011100',
            (4, 10): '1111111110011101',

            (5, 1):  '1111010',
            (5, 2):  '11111110111',
            (5, 3):  '1111111110011110',
            (5, 4):  '1111111110011111',
            (5, 5):  '1111111110100000',
            (5, 6):  '1111111110100001',
            (5, 7):  '1111111110100010',
            (5, 8):  '1111111110100011',
            (5, 9):  '1111111110100100',
            (5, 10): '1111111110100101',

            (6, 1):  '1111011',
            (6, 2):  '111111110110',
            (6, 3):  '1111111110100110',
            (6, 4):  '1111111110100111',
            (6, 5):  '1111111110101000',
            (6, 6):  '1111111110101001',
            (6, 7):  '1111111110101010',
            (6, 8):  '1111111110101011',
            (6, 9):  '1111111110101100',
            (6, 10): '1111111110101101',

            (7, 1):  '11111010',
            (7, 2):  '111111110111',
            (7, 3):  '1111111110101110',
            (7, 4):  '1111111110101111',
            (7, 5):  '1111111110110000',
            (7, 6):  '1111111110110001',
            (7, 7):  '1111111110110010',
            (7, 8):  '1111111110110011',
            (7, 9):  '1111111110110100',
            (7, 10): '1111111110110101',

            (8, 1):  '111111000',
            (8, 2):  '111111111000000',
            (8, 3):  '1111111110110110',
            (8, 4):  '1111111110110111',
            (8, 5):  '1111111110111000',
            (8, 6):  '1111111110111001',
            (8, 7):  '1111111110111010',
            (8, 8):  '1111111110111011',
            (8, 9):  '1111111110111100',
            (8, 10): '1111111110111101',

            (9, 1):  '111111001',
            (9, 2):  '1111111110111110',
            (9, 3):  '1111111110111111',
            (9, 4):  '1111111111000000',
            (9, 5):  '1111111111000001',
            (9, 6):  '1111111111000010',
            (9, 7):  '1111111111000011',
            (9, 8):  '1111111111000100',
            (9, 9):  '1111111111000101',
            (9, 10): '1111111111000110',
            # A
            (10, 1):  '111111010',
            (10, 2):  '1111111111000111',
            (10, 3):  '1111111111001000',
            (10, 4):  '1111111111001001',
            (10, 5):  '1111111111001010',
            (10, 6):  '1111111111001011',
            (10, 7):  '1111111111001100',
            (10, 8):  '1111111111001101',
            (10, 9):  '1111111111001110',
            (10, 10): '1111111111001111',
            # B
            (11, 1):  '1111111001',
            (11, 2):  '1111111111010000',
            (11, 3):  '1111111111010001',
            (11, 4):  '1111111111010010',
            (11, 5):  '1111111111010011',
            (11, 6):  '1111111111010100',
            (11, 7):  '1111111111010101',
            (11, 8):  '1111111111010110',
            (11, 9):  '1111111111010111',
            (11, 10): '1111111111011000',
            # C
            (12, 1):  '1111111010',
            (12, 2):  '1111111111011001',
            (12, 3):  '1111111111011010',
            (12, 4):  '1111111111011011',
            (12, 5):  '1111111111011100',
            (12, 6):  '1111111111011101',
            (12, 7):  '1111111111011110',
            (12, 8):  '1111111111011111',
            (12, 9):  '1111111111100000',
            (12, 10): '1111111111100001',
            # D
            (13, 1):  '11111111000',
            (13, 2):  '1111111111100010',
            (13, 3):  '1111111111100011',
            (13, 4):  '1111111111100100',
            (13, 5):  '1111111111100101',
            (13, 6):  '1111111111100110',
            (13, 7):  '1111111111100111',
            (13, 8):  '1111111111101000',
            (13, 9):  '1111111111101001',
            (13, 10): '1111111111101010',
            # E
            (14, 1):  '1111111111101011',
            (14, 2):  '1111111111101100',
            (14, 3):  '1111111111101101',
            (14, 4):  '1111111111101110',
            (14, 5):  '1111111111101111',
            (14, 6):  '1111111111110000',
            (14, 7):  '1111111111110001',
            (14, 8):  '1111111111110010',
            (14, 9):  '1111111111110011',
            (14, 10): '1111111111110100',
            # F
            (15, 1):  '1111111111110101',
            (15, 2):  '1111111111110110',
            (15, 3):  '1111111111110111',
            (15, 4):  '1111111111111000',
            (15, 5):  '1111111111111001',
            (15, 6):  '1111111111111010',
            (15, 7):  '1111111111111011',
            (15, 8):  '1111111111111100',
            (15, 9):  '1111111111111101',
            (15, 10): '1111111111111110'
        }),
        CHROMINANCE: bidict({
            EOB: '00',  # (0, 0)
            ZRL: '1111111010',  # (F, 0)

            (0, 1):  '01',
            (0, 2):  '100',
            (0, 3):  '1010',
            (0, 4):  '11000',
            (0, 5):  '11001',
            (0, 6):  '111000',
            (0, 7):  '1111000',
            (0, 8):  '111110100',
            (0, 9):  '1111110110',
            (0, 10): '111111110100',

            (1, 1):  '1011',
            (1, 2):  '111001',
            (1, 3):  '11110110',
            (1, 4):  '111110101',
            (1, 5):  '11111110110',
            (1, 6):  '111111110101',
            (1, 7):  '1111111110001000',
            (1, 8):  '1111111110001001',
            (1, 9):  '1111111110001010',
            (1, 10): '1111111110001011',

            (2, 1):  '11010',
            (2, 2):  '11110111',
            (2, 3):  '1111110111',
            (2, 4):  '111111110110',
            (2, 5):  '111111111000010',
            (2, 6):  '1111111110001100',
            (2, 7):  '1111111110001101',
            (2, 8):  '1111111110001110',
            (2, 9):  '1111111110001111',
            (2, 10): '1111111110010000',

            (3, 1):  '11011',
            (3, 2):  '11111000',
            (3, 3):  '1111111000',
            (3, 4):  '111111110111',
            (3, 5):  '1111111110010001',
            (3, 6):  '1111111110010010',
            (3, 7):  '1111111110010011',
            (3, 8):  '1111111110010100',
            (3, 9):  '1111111110010101',
            (3, 10): '1111111110010110',

            (4, 1):  '111010',
            (4, 2):  '111110110',
            (4, 3):  '1111111110010111',
            (4, 4):  '1111111110011000',
            (4, 5):  '1111111110011001',
            (4, 6):  '1111111110011010',
            (4, 7):  '1111111110011011',
            (4, 8):  '1111111110011100',
            (4, 9):  '1111111110011101',
            (4, 10): '1111111110011110',

            (5, 1):  '111011',
            (5, 2):  '1111111001',
            (5, 3):  '1111111110011111',
            (5, 4):  '1111111110100000',
            (5, 5):  '1111111110100001',
            (5, 6):  '1111111110100010',
            (5, 7):  '1111111110100011',
            (5, 8):  '1111111110100100',
            (5, 9):  '1111111110100101',
            (5, 10): '1111111110100110',

            (6, 1):  '1111001',
            (6, 2):  '11111110111',
            (6, 3):  '1111111110100111',
            (6, 4):  '1111111110101000',
            (6, 5):  '1111111110101001',
            (6, 6):  '1111111110101010',
            (6, 7):  '1111111110101011',
            (6, 8):  '1111111110101100',
            (6, 9):  '1111111110101101',
            (6, 10): '1111111110101110',

            (7, 1):  '1111010',
            (7, 2):  '111111110000',
            (7, 3):  '1111111110101111',
            (7, 4):  '1111111110110000',
            (7, 5):  '1111111110110001',
            (7, 6):  '1111111110110010',
            (7, 7):  '1111111110110011',
            (7, 8):  '1111111110110100',
            (7, 9):  '1111111110110101',
            (7, 10): '1111111110110110',

            (8, 1):  '11111001',
            (8, 2):  '1111111110110111',
            (8, 3):  '1111111110111000',
            (8, 4):  '1111111110111001',
            (8, 5):  '1111111110111010',
            (8, 6):  '1111111110111011',
            (8, 7):  '1111111110111100',
            (8, 8):  '1111111110111101',
            (8, 9):  '1111111110111110',
            (8, 10): '1111111110111111',

            (9, 1):  '111110111',
            (9, 2):  '1111111111000000',
            (9, 3):  '1111111111000001',
            (9, 4):  '1111111111000010',
            (9, 5):  '1111111111000011',
            (9, 6):  '1111111111000100',
            (9, 7):  '1111111111000101',
            (9, 8):  '1111111111000110',
            (9, 9):  '1111111111000111',
            (9, 10): '1111111111001000',
            # A
            (10, 1):  '111111000',
            (10, 2):  '1111111111001001',
            (10, 3):  '1111111111001010',
            (10, 4):  '1111111111001011',
            (10, 5):  '1111111111001100',
            (10, 6):  '1111111111001101',
            (10, 7):  '1111111111001110',
            (10, 8):  '1111111111001111',
            (10, 9):  '1111111111010000',
            (10, 10): '1111111111010001',
            # B
            (11, 1):  '111111001',
            (11, 2):  '1111111111010010',
            (11, 3):  '1111111111010011',
            (11, 4):  '1111111111010100',
            (11, 5):  '1111111111010101',
            (11, 6):  '1111111111010110',
            (11, 7):  '1111111111010111',
            (11, 8):  '1111111111011000',
            (11, 9):  '1111111111011001',
            (11, 10): '1111111111011010',
            # C
            (12, 1):  '111111010',
            (12, 2):  '1111111111011011',
            (12, 3):  '1111111111011100',
            (12, 4):  '1111111111011101',
            (12, 5):  '1111111111011110',
            (12, 6):  '1111111111011111',
            (12, 7):  '1111111111100000',
            (12, 8):  '1111111111100001',
            (12, 9):  '1111111111100010',
            (12, 10): '1111111111100011',
            # D
            (13, 1):  '11111111001',
            (13, 2):  '1111111111100100',
            (13, 3):  '1111111111100101',
            (13, 4):  '1111111111100110',
            (13, 5):  '1111111111100111',
            (13, 6):  '1111111111101000',
            (13, 7):  '1111111111101001',
            (13, 8):  '1111111111101010',
            (13, 9):  '1111111111101011',
            (13, 10): '1111111111101100',
            # E
            (14, 1):  '11111111100000',
            (14, 2):  '1111111111101101',
            (14, 3):  '1111111111101110',
            (14, 4):  '1111111111101111',
            (14, 5):  '1111111111110000',
            (14, 6):  '1111111111110001',
            (14, 7):  '1111111111110010',
            (14, 8):  '1111111111110011',
            (14, 9):  '1111111111110100',
            (14, 10): '1111111111110101',
            # F
            (15, 1):  '111111111000011',
            (15, 2):  '1111111111110110',
            (15, 3):  '1111111111110111',
            (15, 4):  '1111111111111000',
            (15, 5):  '1111111111111001',
            (15, 6):  '1111111111111010',
            (15, 7):  '1111111111111011',
            (15, 8):  '1111111111111100',
            (15, 9):  '1111111111111101',
            (15, 10): '1111111111111110'
        })
    }
}
