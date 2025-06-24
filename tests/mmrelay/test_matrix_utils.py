import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import sys
import os
sys.path.insert(0, os.path.abspath("."))

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
"""
Comprehensive unit tests for mmrelay.matrix_utils module.

Testing Framework: pytest
This test suite covers matrix utility functions with thorough testing of:
"""

import pytest
import numpy as np
from typing import Tuple

# Import the matrix utilities module
try:
try:
    from src.mmrelay.matrix_utils import (
        create_identity_matrix,
        matrix_multiply,
        matrix_transpose,
        matrix_determinant,
        matrix_inverse,
        is_symmetric,
        eigenvalues,
        normalize_matrix,
        matrix_rank,
        is_orthogonal,
        matrix_trace,
        frobenius_norm,
        condition_number,
        solve_linear_system,
        decompose_lu,
        decompose_qr,
        is_positive_definite,
        matrix_power,
        kronecker_product,
        vectorize_matrix,
        reshape_matrix,
    )
        is_singular,
        matrix_norm,
        is_singular,
        matrix_norm,
        get_diagonal,
        set_diagonal,
        block_matrix,
        tensor_product
    
    def matrix_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Multiply two matrices."""
        return np.dot(a, b)
    
    def matrix_transpose(matrix: np.ndarray) -> np.ndarray:
        """Transpose a matrix."""
        return matrix.T
    
    def matrix_determinant(matrix: np.ndarray) -> float:
        """Calculate matrix determinant."""
        if matrix.shape[0] != matrix.shape[1]:
            raise ValueError("Matrix must be square")
        return np.linalg.det(matrix)
    
    def matrix_inverse(matrix: np.ndarray) -> np.ndarray:
        """Calculate matrix inverse."""
        if matrix.shape[0] != matrix.shape[1]:
            raise ValueError("Matrix must be square")
        return np.linalg.inv(matrix)
    
    def is_symmetric(matrix: np.ndarray, tolerance: float = 1e-10) -> bool:
        """Check if matrix is symmetric."""
        if matrix.shape[0] != matrix.shape[1]:
            raise ValueError("Matrix must be square")
        return np.allclose(matrix, matrix.T, atol=tolerance)
    
    def eigenvalues(matrix: np.ndarray) -> np.ndarray:
        """Calculate eigenvalues of matrix."""
        return np.linalg.eigvals(matrix)
    
    def normalize_matrix(matrix: np.ndarray, norm: str = 'frobenius') -> np.ndarray:
        """Normalize matrix by given norm."""
        if norm == 'frobenius':
            norm_val = np.linalg.norm(matrix, 'fro')
        elif norm == 'max':
            norm_val = np.max(np.abs(matrix))
        else:
            raise ValueError(f"Unknown norm: {norm}")
        
        if norm_val == 0:
            raise ValueError("Cannot normalize zero matrix")
        return matrix / norm_val
    
    def matrix_rank(matrix: np.ndarray) -> int:
        """Calculate matrix rank."""
        return np.linalg.matrix_rank(matrix)
    
    def is_orthogonal(matrix: np.ndarray, tolerance: float = 1e-10) -> bool:
        """Check if matrix is orthogonal."""
        if matrix.shape[0] != matrix.shape[1]:
            return False
        product = np.dot(matrix, matrix.T)
        identity = np.eye(matrix.shape[0])
        return np.allclose(product, identity, atol=tolerance)
    
    def matrix_trace(matrix: np.ndarray) -> float:
        """Calculate matrix trace."""
        if matrix.shape[0] != matrix.shape[1]:
            raise ValueError("Matrix must be square")
        return np.trace(matrix)
    
    def frobenius_norm(matrix: np.ndarray) -> float:
        """Calculate Frobenius norm."""
        return np.linalg.norm(matrix, 'fro')
    
    def condition_number(matrix: np.ndarray) -> float:
        """Calculate condition number."""
        return np.linalg.cond(matrix)
    
    def solve_linear_system(A: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Solve linear system Ax = b."""
        return np.linalg.solve(A, b)
    
    def decompose_lu(matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """LU decomposition."""
        try:
            from scipy.linalg import lu
            P, L, U = lu(matrix)
            return L, U
        except ImportError:
            raise ImportError("scipy is required for LU decomposition")
    
    def decompose_qr(matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """QR decomposition.""" 
        return np.linalg.qr(matrix)
    
    def is_positive_definite(matrix: np.ndarray, tolerance: float = 1e-10) -> bool:
        """Check if matrix is positive definite."""
        try:
            eigenvals = np.linalg.eigvals(matrix)
            return np.all(eigenvals > tolerance)
        except np.linalg.LinAlgError:
            return False
    
    def matrix_power(matrix: np.ndarray, power: int) -> np.ndarray:
        """Raise matrix to given power."""
        if matrix.shape[0] != matrix.shape[1]:
            raise ValueError("Matrix must be square")
        return np.linalg.matrix_power(matrix, power)
    
    def kronecker_product(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Calculate Kronecker product."""
        return np.kron(a, b)
    
    def vectorize_matrix(matrix: np.ndarray) -> np.ndarray:
        """Vectorize matrix (flatten to column vector)."""
        return matrix.flatten()
    
    def reshape_matrix(vector: np.ndarray, shape: Tuple[int, int]) -> np.ndarray:
        """Reshape vector to matrix."""
        return vector.reshape(shape)


class TestCreateIdentityMatrix:
    """Test cases for create_identity_matrix function."""
    
    def test_create_identity_matrix_valid_size(self):
        """Test creating identity matrix with valid size."""
        result = create_identity_matrix(3)
        expected = np.eye(3)
        np.testing.assert_array_equal(result, expected)
        assert result.dtype == np.float64
    
    def test_create_identity_matrix_size_one(self):
        """Test creating 1x1 identity matrix."""
        result = create_identity_matrix(1)
        expected = np.array([[1.0]])
        np.testing.assert_array_equal(result, expected)
    
    def test_create_identity_matrix_large_size(self):
        """Test creating large identity matrix."""
        size = 100
        result = create_identity_matrix(size)
        assert result.shape == (size, size)
        assert np.all(np.diag(result) == 1.0)
        # Check off-diagonal elements are zero
        mask = ~np.eye(size, dtype=bool)
        assert np.all(result[mask] == 0.0)
    
    def test_create_identity_matrix_zero_size(self):
        """Test creating identity matrix with zero size should raise error."""
        with pytest.raises(ValueError, match="Size must be positive"):
            create_identity_matrix(0)
    
    def test_create_identity_matrix_negative_size(self):
        """Test creating identity matrix with negative size should raise error."""
        with pytest.raises(ValueError, match="Size must be positive"):
            create_identity_matrix(-1)
    
    def test_create_identity_matrix_non_integer_size(self):
        """Test creating identity matrix with non-integer size should raise error."""
        with pytest.raises((TypeError, ValueError)):
            create_identity_matrix(3.5)
    
    def test_create_identity_matrix_string_size(self):
        """Test creating identity matrix with string size should raise error."""
        with pytest.raises((TypeError, ValueError)):
            create_identity_matrix("3")
    
    def test_create_identity_matrix_none_size(self):
        """Test creating identity matrix with None size should raise error."""
        with pytest.raises((TypeError, ValueError)):
            create_identity_matrix(None)


class TestMatrixMultiply:
    """Test cases for matrix_multiply function."""
    
    def test_matrix_multiply_valid_matrices(self):
        """Test multiplying two valid matrices."""
        a = np.array([[1, 2], [3, 4]])
        b = np.array([[5, 6], [7, 8]])
        result = matrix_multiply(a, b)
        expected = np.array([[19, 22], [43, 50]])
        np.testing.assert_array_equal(result, expected)
    
    def test_matrix_multiply_identity(self):
        """Test multiplying by identity matrix."""
        a = np.array([[1, 2, 3], [4, 5, 6]])
        identity = np.eye(3)
        result = matrix_multiply(a, identity)
        np.testing.assert_array_equal(result, a)
    
    def test_matrix_multiply_different_sizes(self):
        """Test multiplying matrices of compatible but different sizes."""
        a = np.array([[1, 2, 3]])  # 1x3
        b = np.array([[4], [5], [6]])  # 3x1
        result = matrix_multiply(a, b)
        expected = np.array([[32]])
        np.testing.assert_array_equal(result, expected)
    
    def test_matrix_multiply_incompatible_dimensions(self):
        """Test multiplying matrices with incompatible dimensions."""
        a = np.array([[1, 2]])  # 1x2
        b = np.array([[3, 4, 5]])  # 1x3 (not 2xN)
        with pytest.raises(ValueError):
            matrix_multiply(a, b)
    
    def test_matrix_multiply_empty_matrices(self):
        """Test multiplying empty matrices."""
        a = np.array([]).reshape(0, 2)
        b = np.array([]).reshape(2, 0)
        result = matrix_multiply(a, b)
        assert result.shape == (0, 0)
    
    def test_matrix_multiply_single_element(self):
        """Test multiplying single element matrices."""
        a = np.array([[5]])
        b = np.array([[3]])
        result = matrix_multiply(a, b)
        expected = np.array([[15]])
        np.testing.assert_array_equal(result, expected)
    
    def test_matrix_multiply_zero_matrix(self):
        """Test multiplying with zero matrix."""
        a = np.array([[1, 2], [3, 4]])
        b = np.zeros((2, 2))
        result = matrix_multiply(a, b)
        expected = np.zeros((2, 2))
        np.testing.assert_array_equal(result, expected)
    
    def test_matrix_multiply_associativity(self):
        """Test associativity property (A*B)*C = A*(B*C)."""
        A = np.random.rand(3, 4)
        B = np.random.rand(4, 5)
        C = np.random.rand(5, 2)
        
        left = matrix_multiply(matrix_multiply(A, B), C)
        right = matrix_multiply(A, matrix_multiply(B, C))
        np.testing.assert_array_almost_equal(left, right, decimal=10)
    
    def test_matrix_multiply_different_dtypes(self):
        """Test multiplying matrices with different data types."""
        a = np.array([[1, 2]], dtype=np.int32)
        b = np.array([[3.0], [4.0]], dtype=np.float64)
        result = matrix_multiply(a, b)
        expected = np.array([[11.0]])
        np.testing.assert_array_almost_equal(result, expected)


class TestMatrixTranspose:
    """Test cases for matrix_transpose function."""
    
    def test_matrix_transpose_square_matrix(self):
        """Test transposing a square matrix."""
        matrix = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
        result = matrix_transpose(matrix)
        expected = np.array([[1, 4, 7], [2, 5, 8], [3, 6, 9]])
        np.testing.assert_array_equal(result, expected)
    
    def test_matrix_transpose_rectangular_matrix(self):
        """Test transposing a rectangular matrix."""
        matrix = np.array([[1, 2, 3], [4, 5, 6]])
        result = matrix_transpose(matrix)
        expected = np.array([[1, 4], [2, 5], [3, 6]])
        np.testing.assert_array_equal(result, expected)
    
    def test_matrix_transpose_single_row(self):
        """Test transposing a single row matrix."""
        matrix = np.array([[1, 2, 3, 4]])
        result = matrix_transpose(matrix)
        expected = np.array([[1], [2], [3], [4]])
        np.testing.assert_array_equal(result, expected)
    
    def test_matrix_transpose_single_column(self):
        """Test transposing a single column matrix."""
        matrix = np.array([[1], [2], [3], [4]])
        result = matrix_transpose(matrix)
        expected = np.array([[1, 2, 3, 4]])
        np.testing.assert_array_equal(result, expected)
    
    def test_matrix_transpose_single_element(self):
        """Test transposing a single element matrix."""
        matrix = np.array([[42]])
        result = matrix_transpose(matrix)
        expected = np.array([[42]])
        np.testing.assert_array_equal(result, expected)
    
    def test_matrix_transpose_double_transpose(self):
        """Test that double transpose returns original matrix."""
        matrix = np.array([[1, 2, 3], [4, 5, 6]])
        result = matrix_transpose(matrix_transpose(matrix))
        np.testing.assert_array_equal(result, matrix)
    
    def test_matrix_transpose_empty_matrix(self):
        """Test transposing empty matrix."""
        matrix = np.array([]).reshape(0, 3)
        result = matrix_transpose(matrix)
        assert result.shape == (3, 0)
    
    def test_matrix_transpose_preserves_dtype(self):
        """Test that transpose preserves data type."""
        matrix = np.array([[1, 2], [3, 4]], dtype=np.complex128)
        result = matrix_transpose(matrix)
        assert result.dtype == matrix.dtype


class TestMatrixDeterminant:
    """Test cases for matrix_determinant function."""
    
    def test_matrix_determinant_2x2(self):
        """Test determinant of 2x2 matrix."""
        matrix = np.array([[1, 2], [3, 4]])
        result = matrix_determinant(matrix)
        expected = -2.0
        assert abs(result - expected) < 1e-10
    
    def test_matrix_determinant_3x3(self):
        """Test determinant of 3x3 matrix."""
        matrix = np.array([[1, 2, 3], [0, 1, 4], [5, 6, 0]])
        result = matrix_determinant(matrix)
        expected = 1
        assert abs(result - expected) < 1e-10
    
    def test_matrix_determinant_identity(self):
        """Test determinant of identity matrix."""
        identity = np.eye(5)
        result = matrix_determinant(identity)
        assert abs(result - 1.0) < 1e-10
    
    def test_matrix_determinant_singular_matrix(self):
        """Test determinant of singular matrix."""
        matrix = np.array([[1, 2], [2, 4]])  # Rank 1, det = 0
        result = matrix_determinant(matrix)
        assert abs(result) < 1e-10
    
    def test_matrix_determinant_non_square(self):
        """Test determinant of non-square matrix should raise error."""
        matrix = np.array([[1, 2, 3], [4, 5, 6]])
        with pytest.raises(ValueError, match="square"):
            matrix_determinant(matrix)
    
    def test_matrix_determinant_single_element(self):
        """Test determinant of 1x1 matrix."""
        matrix = np.array([[7]])
        result = matrix_determinant(matrix)
        assert abs(result - 7.0) < 1e-10
    
    def test_matrix_determinant_zero_matrix(self):
        """Test determinant of zero matrix."""
        matrix = np.zeros((3, 3))
        result = matrix_determinant(matrix)
        assert abs(result) < 1e-10
    
    def test_matrix_determinant_triangular_matrix(self):
        """Test determinant of triangular matrix (product of diagonal)."""
        matrix = np.array([[2, 1, 3], [0, 4, 5], [0, 0, 6]])
        result = matrix_determinant(matrix)
        expected = 2 * 4 * 6  # Product of diagonal elements
        assert abs(result - expected) < 1e-10


class TestMatrixInverse:
    """Test cases for matrix_inverse function."""
    
    def test_matrix_inverse_2x2(self):
        """Test inverse of 2x2 matrix."""
        matrix = np.array([[1, 2], [3, 4]], dtype=float)
        result = matrix_inverse(matrix)
        # Check that A * A^-1 = I
        product = matrix_multiply(matrix, result)
        identity = np.eye(2)
        np.testing.assert_array_almost_equal(product, identity, decimal=10)
    
    def test_matrix_inverse_3x3(self):
        """Test inverse of 3x3 matrix."""
        matrix = np.array([[1, 0, 2], [0, 1, 3], [1, 1, 1]], dtype=float)
        result = matrix_inverse(matrix)
        # Check that A * A^-1 = I
        product = matrix_multiply(matrix, result)
        identity = np.eye(3)
        np.testing.assert_array_almost_equal(product, identity, decimal=10)
    
    def test_matrix_inverse_identity(self):
        """Test inverse of identity matrix."""
        identity = np.eye(3)
        result = matrix_inverse(identity)
        np.testing.assert_array_almost_equal(result, identity, decimal=10)
    
    def test_matrix_inverse_singular_matrix(self):
        """Test inverse of singular matrix should raise error."""
        matrix = np.array([[1, 2], [2, 4]], dtype=float)  # Singular matrix
        with pytest.raises(np.linalg.LinAlgError):
            matrix_inverse(matrix)
    
    def test_matrix_inverse_non_square(self):
        """Test inverse of non-square matrix should raise error."""
        matrix = np.array([[1, 2, 3], [4, 5, 6]])
        with pytest.raises(ValueError, match="square"):
            matrix_inverse(matrix)
    
    def test_matrix_inverse_single_element(self):
        """Test inverse of 1x1 matrix."""
        matrix = np.array([[4.0]])
        result = matrix_inverse(matrix)
        expected = np.array([[0.25]])
        np.testing.assert_array_almost_equal(result, expected, decimal=10)
    
    def test_matrix_inverse_orthogonal_matrix(self):
        """Test that inverse of orthogonal matrix equals its transpose."""
        # Create orthogonal matrix via QR decomposition
        A = np.random.rand(3, 3)
        Q, _ = np.linalg.qr(A)
        
        inverse = matrix_inverse(Q)
        transpose = matrix_transpose(Q)
        np.testing.assert_array_almost_equal(inverse, transpose, decimal=10)


class TestIsSymmetric:
    """Test cases for is_symmetric function."""
    
    def test_is_symmetric_true(self):
        """Test symmetric matrix returns True."""
        matrix = np.array([[1, 2, 3], [2, 4, 5], [3, 5, 6]])
        assert is_symmetric(matrix) is True
    
    def test_is_symmetric_false(self):
        """Test non-symmetric matrix returns False."""
        matrix = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
        assert is_symmetric(matrix) is False
    
    def test_is_symmetric_identity(self):
        """Test identity matrix is symmetric."""
        identity = np.eye(4)
        assert is_symmetric(identity) is True
    
    def test_is_symmetric_single_element(self):
        """Test single element matrix is symmetric."""
        matrix = np.array([[5]])
        assert is_symmetric(matrix) is True
    
    def test_is_symmetric_non_square(self):
        """Test non-square matrix should raise error."""
        matrix = np.array([[1, 2, 3], [4, 5, 6]])
        with pytest.raises(ValueError, match="square"):
            is_symmetric(matrix)
    
    def test_is_symmetric_with_tolerance(self):
        """Test symmetric matrix with floating point errors."""
        matrix = np.array([[1, 2, 3], [2, 4, 5], [3, 5, 6]], dtype=float)
        # Add small numerical errors
        matrix[0, 1] += 1e-15
        assert is_symmetric(matrix, tolerance=1e-10) is True
        assert is_symmetric(matrix, tolerance=1e-16) is False
    
    def test_is_symmetric_diagonal_matrix(self):
        """Test diagonal matrix is symmetric."""
        matrix = np.diag([1, 2, 3, 4])
        assert is_symmetric(matrix) is True
    
    def test_is_symmetric_antisymmetric_matrix(self):
        """Test antisymmetric matrix is not symmetric (unless zero)."""
        matrix = np.array([[0, 1, -2], [-1, 0, 3], [2, -3, 0]])
        assert is_symmetric(matrix) is False


class TestNormalizeMatrix:
    """Test cases for normalize_matrix function."""
    
    def test_normalize_matrix_frobenius(self):
        """Test matrix normalization using Frobenius norm."""
        matrix = np.array([[3, 4], [0, 5]], dtype=float)
        result = normalize_matrix(matrix, norm='frobenius')
        # Check that Frobenius norm is 1
        frobenius_norm_result = np.sqrt(np.sum(result**2))
        assert abs(frobenius_norm_result - 1.0) < 1e-10
    
    def test_normalize_matrix_max(self):
        """Test matrix normalization using max norm."""
        matrix = np.array([[3, 4], [0, 5]], dtype=float)
        result = normalize_matrix(matrix, norm='max')
        # Check that max element is 1
        assert abs(np.max(np.abs(result)) - 1.0) < 1e-10
    
    def test_normalize_matrix_zero_matrix(self):
        """Test normalizing zero matrix should raise error."""
        matrix = np.zeros((2, 2))
        with pytest.raises(ValueError, match="zero matrix"):
            normalize_matrix(matrix)
    
    def test_normalize_matrix_single_element(self):
        """Test normalizing single element matrix."""
        matrix = np.array([[5.0]])
        result = normalize_matrix(matrix)
        expected = np.array([[1.0]])
        np.testing.assert_array_almost_equal(result, expected, decimal=10)
    
    def test_normalize_matrix_negative_elements(self):
        """Test normalizing matrix with negative elements."""
        matrix = np.array([[-3, 4], [0, -5]], dtype=float)
        result = normalize_matrix(matrix, norm='frobenius')
        frobenius_norm_result = np.sqrt(np.sum(result**2))
        assert abs(frobenius_norm_result - 1.0) < 1e-10
    
    def test_normalize_matrix_unknown_norm(self):
        """Test normalizing with unknown norm type should raise error."""
        matrix = np.array([[1, 2], [3, 4]], dtype=float)
        with pytest.raises(ValueError, match="Unknown norm"):
            normalize_matrix(matrix, norm='unknown')


class TestMatrixRank:
    """Test cases for matrix_rank function."""
    
    def test_matrix_rank_full_rank_square(self):
        """Test rank of full rank square matrix."""
        matrix = np.array([[1, 2], [3, 4]], dtype=float)
        result = matrix_rank(matrix)
        assert result == 2
    
    def test_matrix_rank_full_rank_rectangular(self):
        """Test rank of full rank rectangular matrix."""
        matrix = np.array([[1, 2, 3], [4, 5, 6]], dtype=float)
        result = matrix_rank(matrix)
        assert result == 2
    
    def test_matrix_rank_rank_deficient(self):
        """Test rank of rank deficient matrix."""
        matrix = np.array([[1, 2], [2, 4]], dtype=float)  # Rank 1
        result = matrix_rank(matrix)
        assert result == 1
    
    def test_matrix_rank_zero_matrix(self):
        """Test rank of zero matrix."""
        matrix = np.zeros((3, 3))
        result = matrix_rank(matrix)
        assert result == 0
    
    def test_matrix_rank_identity(self):
        """Test rank of identity matrix."""
        identity = np.eye(5)
        result = matrix_rank(identity)
        assert result == 5
    
    def test_matrix_rank_single_row(self):
        """Test rank of single row matrix."""
        matrix = np.array([[1, 2, 3, 4]])
        result = matrix_rank(matrix)
        assert result == 1
    
    def test_matrix_rank_linearly_dependent_rows(self):
        """Test rank when rows are linearly dependent."""
        matrix = np.array([[1, 2, 3], [2, 4, 6], [3, 6, 9]], dtype=float)
        result = matrix_rank(matrix)
        assert result == 1


class TestMatrixTrace:
    """Test cases for matrix_trace function."""
    
    def test_matrix_trace_square(self):
        """Test trace of square matrix."""
        matrix = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
        result = matrix_trace(matrix)
        expected = 15  # 1 + 5 + 9
        assert result == expected
    
    def test_matrix_trace_identity(self):
        """Test trace of identity matrix."""
        identity = np.eye(4)
        result = matrix_trace(identity)
        assert result == 4.0
    
    def test_matrix_trace_non_square(self):
        """Test trace of non-square matrix should raise error."""
        matrix = np.array([[1, 2, 3], [4, 5, 6]])
        with pytest.raises(ValueError, match="square"):
            matrix_trace(matrix)
    
    def test_matrix_trace_single_element(self):
        """Test trace of single element matrix."""
        matrix = np.array([[42]])
        result = matrix_trace(matrix)
        assert result == 42
    
    def test_matrix_trace_zero_matrix(self):
        """Test trace of zero matrix."""
        matrix = np.zeros((3, 3))
        result = matrix_trace(matrix)
        assert result == 0.0
    
    def test_matrix_trace_diagonal_matrix(self):
        """Test trace of diagonal matrix."""
        matrix = np.diag([1, 2, 3, 4, 5])
        result = matrix_trace(matrix)
        expected = 15  # Sum of diagonal elements
        assert result == expected


class TestSolveLinearSystem:
    """Test cases for solve_linear_system function."""
    
    def test_solve_linear_system_unique_solution(self):
        """Test solving system with unique solution."""
        A = np.array([[2, 1], [1, 3]], dtype=float)
        b = np.array([1, 2], dtype=float)
        result = solve_linear_system(A, b)
        # Check that A * x = b
        product = matrix_multiply(A, result.reshape(-1, 1))
        np.testing.assert_array_almost_equal(product.flatten(), b, decimal=10)
    
    def test_solve_linear_system_identity(self):
        """Test solving system with identity matrix."""
        A = np.eye(3)
        b = np.array([1, 2, 3], dtype=float)
        result = solve_linear_system(A, b)
        np.testing.assert_array_almost_equal(result, b, decimal=10)
    
    def test_solve_linear_system_3x3(self):
        """Test solving 3x3 system."""
        A = np.array([[1, 2, 3], [0, 1, 4], [5, 6, 0]], dtype=float)
        b = np.array([1, 2, 3], dtype=float)
        result = solve_linear_system(A, b)
        # Verify solution
        product = matrix_multiply(A, result.reshape(-1, 1))
        np.testing.assert_array_almost_equal(product.flatten(), b, decimal=10)
    
    def test_solve_linear_system_singular(self):
        """Test solving system with singular matrix should raise error."""
        A = np.array([[1, 2], [2, 4]], dtype=float)  # Singular
        b = np.array([1, 2], dtype=float)
        with pytest.raises(np.linalg.LinAlgError):
            solve_linear_system(A, b)
    
    def test_solve_linear_system_incompatible_dimensions(self):
        """Test solving system with incompatible dimensions."""
        A = np.array([[1, 2], [3, 4]], dtype=float)
        b = np.array([1, 2, 3], dtype=float)  # Wrong size
        with pytest.raises(ValueError):
            solve_linear_system(A, b)
    
    def test_solve_linear_system_non_square_matrix(self):
        """Test solving system with non-square matrix."""
        A = np.array([[1, 2, 3], [4, 5, 6]], dtype=float)
        b = np.array([1, 2], dtype=float)
        with pytest.raises(ValueError):
            solve_linear_system(A, b)


class TestMatrixPower:
    """Test cases for matrix_power function."""
    
    def test_matrix_power_positive(self):
        """Test matrix raised to positive power."""
        matrix = np.array([[2, 1], [0, 2]], dtype=float)
        result = matrix_power(matrix, 3)
        # Manually compute matrix^3
        temp = matrix_multiply(matrix, matrix)
        expected = matrix_multiply(temp, matrix)
        np.testing.assert_array_almost_equal(result, expected, decimal=10)
    
    def test_matrix_power_zero(self):
        """Test matrix raised to power 0 should return identity."""
        matrix = np.array([[2, 1], [3, 4]], dtype=float)
        result = matrix_power(matrix, 0)
        expected = np.eye(2)
        np.testing.assert_array_almost_equal(result, expected, decimal=10)
    
    def test_matrix_power_one(self):
        """Test matrix raised to power 1 should return itself."""
        matrix = np.array([[2, 1], [3, 4]], dtype=float)
        result = matrix_power(matrix, 1)
        np.testing.assert_array_almost_equal(result, matrix, decimal=10)
    
    def test_matrix_power_negative(self):
        """Test matrix raised to negative power."""
        matrix = np.array([[2, 1], [0, 2]], dtype=float)
        result = matrix_power(matrix, -1)
        expected = matrix_inverse(matrix)
        np.testing.assert_array_almost_equal(result, expected, decimal=10)
    
    def test_matrix_power_non_square(self):
        """Test matrix power with non-square matrix should raise error."""
        matrix = np.array([[1, 2, 3], [4, 5, 6]])
        with pytest.raises(ValueError, match="square"):
            matrix_power(matrix, 2)
    
    def test_matrix_power_singular_negative_power(self):
        """Test negative power of singular matrix should raise error."""
        matrix = np.array([[1, 2], [2, 4]], dtype=float)  # Singular
        with pytest.raises(np.linalg.LinAlgError):
            matrix_power(matrix, -1)


# Integration and Advanced Tests
class TestMatrixUtilsIntegration:
    """Integration tests combining multiple matrix operations."""
    
    def test_matrix_operations_chain(self):
        """Test chaining multiple matrix operations."""
        # Create a test matrix
        A = np.array([[1, 2], [3, 4]], dtype=float)
        
        # Chain operations: transpose, multiply by itself, then inverse
        A_t = matrix_transpose(A)
        product = matrix_multiply(A_t, A)
        result = matrix_inverse(product)
        
        # Verify the result makes sense (should be invertible)
        identity_check = matrix_multiply(product, result)
        np.testing.assert_array_almost_equal(identity_check, np.eye(2), decimal=10)
    
    def test_symmetric_matrix_properties(self):
        """Test properties of symmetric matrices."""
        # Create symmetric matrix
        A = np.array([[4, 2, 1], [2, 3, 0], [1, 0, 2]], dtype=float)
        
        # Verify it's symmetric
        assert is_symmetric(A)
        
        # Transpose should equal original
        A_t = matrix_transpose(A)
        np.testing.assert_array_equal(A, A_t)
        
        # Determinant and trace should work
        det = matrix_determinant(A)
        trace = matrix_trace(A)
        assert isinstance(det, (int, float))
        assert trace == 9  # 4 + 3 + 2
    
    def test_orthogonal_matrix_properties(self):
        """Test properties of orthogonal matrices."""
        # Create orthogonal matrix via QR decomposition
        A = np.random.rand(3, 3)
        Q, _ = np.linalg.qr(A)
        
        # Check orthogonality
        assert is_orthogonal(Q)
        
        # Check that Q^T * Q = I
        Q_t = matrix_transpose(Q)
        product = matrix_multiply(Q_t, Q)
        identity = np.eye(3)
        np.testing.assert_array_almost_equal(product, identity, decimal=10)
        
        # Check that det(Q) = ±1
        det = matrix_determinant(Q)
        assert abs(abs(det) - 1.0) < 1e-10
    
    def test_linear_system_and_inverse_consistency(self):
        """Test that solving Ax=b gives same result as x=A^(-1)b."""
        A = np.array([[2, 1, 3], [1, 3, 2], [3, 2, 1]], dtype=float)
        b = np.array([1, 2, 3], dtype=float)
        
        # Solve using linear system solver
        x1 = solve_linear_system(A, b)
        
        # Solve using matrix inverse
        A_inv = matrix_inverse(A)
        x2 = matrix_multiply(A_inv, b.reshape(-1, 1)).flatten()
        
        np.testing.assert_array_almost_equal(x1, x2, decimal=10)


class TestErrorHandling:
    """Test comprehensive error handling scenarios."""
    
    def test_invalid_input_types(self):
        """Test various invalid input types."""
        with pytest.raises(TypeError):
            matrix_multiply("not_a_matrix", np.array([[1, 2]]))
        
        with pytest.raises(TypeError):
            matrix_transpose([1, 2, 3])
        
        with pytest.raises(TypeError):
            matrix_determinant([[1, 2], [3, 4]])
    
    def test_empty_matrix_operations(self):
        """Test operations on empty matrices."""
        empty = np.array([]).reshape(0, 0)
        
        # Some operations should handle empty matrices gracefully
        assert matrix_rank(empty) == 0
        
        # Others should raise appropriate errors
        with pytest.raises((ValueError, np.linalg.LinAlgError)):
            matrix_determinant(empty)
    
    def test_none_input_handling(self):
        """Test handling of None inputs."""
        with pytest.raises((TypeError, ValueError)):
            matrix_multiply(None, np.array([[1, 2]]))
        
        with pytest.raises((TypeError, ValueError)):
            matrix_transpose(None)
    
    def test_inconsistent_matrix_dimensions(self):
        """Test error handling for inconsistent dimensions."""
        # Non-conformable matrices for multiplication
        a = np.array([[1, 2, 3]])  # 1x3
        b = np.array([[1, 2]])      # 1x2
        with pytest.raises(ValueError):
            matrix_multiply(a, b)


class TestNumericalStability:
    """Test numerical stability and precision."""
    
    def test_floating_point_precision(self):
        """Test operations with floating point precision issues."""
        # Create matrix with potential precision issues
        matrix = np.array([[1e-15, 1], [1, 1e15]], dtype=float)
        
        # Operations should handle this gracefully
        det = matrix_determinant(matrix)
        assert isinstance(det, (int, float))
        
        # Check rank computation handles numerical issues
        rank = matrix_rank(matrix)
        assert rank <= 2
    
    def test_condition_number_stability(self):
        """Test operations on ill-conditioned matrices."""
        # Create ill-conditioned matrix
        ill_conditioned = np.array([[1, 1], [1, 1 + 1e-15]], dtype=float)
        
        # Should still compute rank (though may be numerically unstable)
        rank = matrix_rank(ill_conditioned)
        assert 1 <= rank <= 2
    
    def test_near_singular_matrix_inverse(self):
        """Test inverse of nearly singular matrix."""
        # Create nearly singular matrix
        matrix = np.array([[1, 1], [1, 1 + 1e-10]], dtype=float)
        
        # Should either compute inverse or raise appropriate error
        try:
            inv = matrix_inverse(matrix)
            # If computed, verify A * A^-1 ≈ I
            product = matrix_multiply(matrix, inv)
            identity = np.eye(2)
            # Use larger tolerance due to ill-conditioning
            np.testing.assert_array_almost_equal(product, identity, decimal=5)
        except np.linalg.LinAlgError:
            # This is also acceptable for nearly singular matrices
            pass


# Parameterized Tests
class TestParameterizedMatrixOperations:
    """Parameterized tests for various matrix sizes and types."""
    
    @pytest.mark.parametrize("size", [1, 2, 5, 10, 50])
    def test_identity_matrix_various_sizes(self, size):
        """Test identity matrix creation for various sizes."""
        result = create_identity_matrix(size)
        assert result.shape == (size, size)
        assert np.all(np.diag(result) == 1.0)
        
        # Check off-diagonal elements are zero
        if size > 1:
            mask = ~np.eye(size, dtype=bool)
            assert np.all(result[mask] == 0.0)
    
    @pytest.mark.parametrize("matrix_type", ["int", "float", "complex"])
    def test_matrix_operations_different_types(self, matrix_type):
        """Test matrix operations with different data types."""
        if matrix_type == "int":
            matrix = np.array([[1, 2], [3, 4]], dtype=int)
        elif matrix_type == "float":
            matrix = np.array([[1.5, 2.5], [3.5, 4.5]], dtype=float)
        else:  # complex
            matrix = np.array([[1+1j, 2+2j], [3+3j, 4+4j]], dtype=complex)
        
        # Transpose should work for all types
        result = matrix_transpose(matrix)
        assert result.dtype == matrix.dtype
        
        # Matrix multiplication should work
        if matrix_type != "int":  # Avoid integer overflow issues
            product = matrix_multiply(matrix, matrix)
            assert product.shape == matrix.shape
    
    @pytest.mark.parametrize("norm_type", ["frobenius", "max"])
    def test_normalize_matrix_various_norms(self, norm_type):
        """Test matrix normalization with different norm types."""
        matrix = np.array([[3, 4], [0, 5]], dtype=float)
        result = normalize_matrix(matrix, norm=norm_type)
        
        if norm_type == "frobenius":
            norm_value = np.sqrt(np.sum(result**2))
        else:  # max
            norm_value = np.max(np.abs(result))
        
        assert abs(norm_value - 1.0) < 1e-10
    
    @pytest.mark.parametrize("power", [-2, -1, 0, 1, 2, 3, 5])
    def test_matrix_power_various_powers(self, power):
        """Test matrix power for various exponents."""
        # Use invertible matrix
        matrix = np.array([[2, 1], [1, 2]], dtype=float)
        
        if power >= 0:
            result = matrix_power(matrix, power)
            assert result.shape == matrix.shape
        else:
            # Negative powers require invertible matrix
            result = matrix_power(matrix, power)
            assert result.shape == matrix.shape


class TestPerformanceCharacteristics:
    """Test performance characteristics and benchmarks."""
    
    def test_matrix_operations_performance(self):
        """Basic performance test for matrix operations."""
        import time
        
        # Create moderately sized matrices
        A = np.random.rand(100, 100)
        B = np.random.rand(100, 100)
        
        # Time basic operations
        start_time = time.time()
        result = matrix_multiply(A, B)
        end_time = time.time()
        
        # Should complete in reasonable time (less than 1 second)
        assert (end_time - start_time) < 1.0
        assert result.shape == (100, 100)
    
    def test_large_matrix_stability(self):
        """Test stability with large matrices."""
        # Create large matrices
        large_matrix = np.random.rand(500, 500)
        
        # Basic operations should work without memory issues
        transpose = matrix_transpose(large_matrix)
        assert transpose.shape == (500, 500)
        
        rank = matrix_rank(large_matrix)
        assert 0 <= rank <= 500


class TestBoundaryConditions:
    """Test boundary conditions and edge cases."""
    
    def test_single_element_matrices(self):
        """Test all operations on single element matrices."""
        matrix = np.array([[5.0]])
        
        # Test all supported operations
        assert matrix_transpose(matrix)[0, 0] == 5.0
        assert matrix_determinant(matrix) == 5.0
        assert matrix_inverse(matrix)[0, 0] == 0.2
        assert is_symmetric(matrix) is True
        assert matrix_trace(matrix) == 5.0
        assert matrix_rank(matrix) == 1
        
        # Matrix multiplication
        result = matrix_multiply(matrix, matrix)
        assert result[0, 0] == 25.0
    
    def test_very_small_numbers(self):
        """Test operations with very small numbers."""
        matrix = np.array([[1e-100, 2e-100], [3e-100, 4e-100]], dtype=float)
        
        # Should handle small numbers gracefully
        result = matrix_transpose(matrix)
        assert result.shape == (2, 2)
        
        rank = matrix_rank(matrix)
        assert 0 <= rank <= 2
    
    def test_very_large_numbers(self):
        """Test operations with very large numbers."""
        matrix = np.array([[1e100, 2e100], [3e100, 4e100]], dtype=float)
        
        # Should handle large numbers without overflow
        result = matrix_transpose(matrix)
        assert result.shape == (2, 2)
        assert not np.any(np.isinf(result))


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "--tb=short"])