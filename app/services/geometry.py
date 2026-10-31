"""Small bounding-box helpers. Boxes are [x0, y0, x1, y1] in PDF points (top-left origin)."""


def area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def intersection(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def intersection_area(a, b):
    inter = intersection(a, b)
    return area(inter) if inter else 0.0


def iou(a, b):
    inter = intersection_area(a, b)
    if inter == 0:
        return 0.0
    return inter / (area(a) + area(b) - inter)


def union(boxes):
    boxes = list(boxes)
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def center(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def contains_point(b, pt, tol=0.0):
    return b[0] - tol <= pt[0] <= b[2] + tol and b[1] - tol <= pt[1] <= b[3] + tol


def h_overlap(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def v_overlap(a, b):
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def pad(b, p, clip=None):
    out = [b[0] - p, b[1] - p, b[2] + p, b[3] + p]
    if clip:
        out = [max(out[0], clip[0]), max(out[1], clip[1]), min(out[2], clip[2]), min(out[3], clip[3])]
    return out
