import sys
import logging

from .chrome_printtopdf import logger, get_pdf_with_chrome_sync


def main():
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.DEBUG)
    logger.addHandler(ch)

    chrome_binary = sys.argv[1]

    pdf_file = get_pdf_with_chrome_sync(sys.argv[2],
        chrome_binary=chrome_binary)
    with open(sys.argv[3], 'wb') as f:
        f.write(pdf_file.read())


if __name__ == '__main__':
    main()
